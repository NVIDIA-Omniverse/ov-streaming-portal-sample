# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.
import logging
import os
from collections.abc import Mapping

import httpx
import yaml
from opentelemetry import metrics
from opentelemetry.metrics import Observation
from opentelemetry.sdk.metrics import (
    MeterProvider,
    Counter,
    UpDownCounter,
    Histogram,
)
from opentelemetry.sdk.metrics.export import (
    PeriodicExportingMetricReader,
    AggregationTemporality,
)
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
    OTLPMetricExporter,
)
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.models import SessionModel
from app.settings import settings

otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv(
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"
)

metric_readers = []
if otlp_endpoint:
    metric_exporter = OTLPMetricExporter(
        preferred_temporality={
            Counter: AggregationTemporality.CUMULATIVE,
            UpDownCounter: AggregationTemporality.CUMULATIVE,
            Histogram: AggregationTemporality.DELTA,
        }
    )
    metric_readers.append(
        PeriodicExportingMetricReader(exporter=metric_exporter)
    )

metrics_provider = MeterProvider(
    metric_readers=metric_readers,
)

metrics.set_meter_provider(metrics_provider)

meter = metrics.get_meter("backend.metrics")

active_sessions = meter.create_up_down_counter(
    "sessions.active.count",
    unit="sessions",
    description="Current amount of active streaming sessions.",
)
session_start = meter.create_counter(
    "sessions.start.count",
    unit="sessions",
    description="Total amount of started streaming sessions.",
)
session_end = meter.create_counter(
    "sessions.end.count",
    unit="sessions",
    description="Total amount of ended streaming sessions.",
)
session_duration = meter.create_histogram(
    "sessions.duration",
    unit="seconds",
    description="Session duration in seconds.",
)


class ClusterQuota(BaseModel):
    names: str
    limit: int


class GpuQuota(BaseModel):
    name: str
    clusters: list[ClusterQuota] | None = None


class GpuQuotaResponse(BaseModel):
    gpus: list[GpuQuota]


def get_total_gpus(options):
    if not settings.ngc_endpoint or not settings.ngc_org:
        return

    with httpx.Client() as client:
        try:
            response = client.get(
                f"{settings.ngc_endpoint}/v2/orgs/{settings.ngc_org}/ngc/nvcf/gpu/quota/rules",
                headers={
                    "Authorization": f"Bearer {settings.nvcf_api_key}",
                },
                timeout=15,
            )
            if response.status_code != 200:
                logging.error(
                    f"Failed to get GPU information: HTTP{response.status_code} -- "
                    f"{response.text}"
                )
                return
        except TimeoutError:
            logging.debug(f"Failed to get GPU information: timeout.")
            return

        body = yaml.safe_load(response.text)
        quota = GpuQuotaResponse.model_validate(body)

        for gpu in quota.gpus:
            if gpu.clusters is not None:
                for cluster in gpu.clusters:
                    yield Observation(
                        cluster.limit,
                        attributes(
                            {"cluster": cluster.names, "gpu": gpu.name}
                        ),
                    )


gpu_total = meter.create_observable_gauge(
    "gpu.total.count",
    unit="gpus",
    description="Total number of GPUs available for the NGC organization.",
    callbacks=[get_total_gpus],
)


class GpuClusterUsage(BaseModel):
    min_instances: int
    max_instances: int
    active_instances: int
    available_instances: int
    active_gpus: int
    available_gpus: int

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class GpuCluster(BaseModel):
    cluster_id: str
    cluster_name: str
    usage: GpuClusterUsage

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class GpuInstanceRegion(BaseModel):
    region_name: str
    clusters: list[GpuCluster]

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class GpuInstance(BaseModel):
    instance_name: str
    value: str
    regions: list[GpuInstanceRegion]

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class InstanceTypeResponse(BaseModel):
    gpus: dict[str, list[GpuInstance]]


def get_active_gpus(options):
    if not settings.ngc_endpoint or not settings.ngc_org:
        return

    with httpx.Client() as client:
        try:
            response = client.get(
                f"{settings.ngc_endpoint}/v3/orgs/{settings.ngc_org}/ngc/nvcf/deployments/instanceTypes",
                headers={
                    "Authorization": f"Bearer {settings.nvcf_api_key}",
                },
                timeout=15,
            )
            if response.status_code != 200:
                logging.error(
                    f"Failed to get active gpus: HTTP{response.status_code} -- "
                    f"{response.text}"
                )
                return
        except TimeoutError:
            logging.debug(f"Failed to get active gpus: timeout.")
            return

        body = response.json()
        instances = InstanceTypeResponse.model_validate({"gpus": body})

        for gpu, instances in instances.gpus.items():
            for instance in instances:
                for region in instance.regions:
                    for cluster in region.clusters:
                        yield Observation(
                            cluster.usage.active_gpus,
                            attributes({
                                "gpu": gpu,
                                "instance": instance.instance_name,
                                "region": region.region_name,
                                "cluster": cluster.cluster_name,
                            }),
                        )


gpu_active = meter.create_observable_gauge(
    "gpu.active.count",
    unit="gpus",
    description="GPUs currently assigned to active NVCF functions.",
    callbacks=[get_active_gpus],
)


def emit_session_end_metrics(session: SessionModel):
    metric_attrs = attributes({
        "session.app": session.app_id,
        "session.user": session.user_id,
        "session.username": session.user_name,
        "nvcf.function_id": str(session.function_id),
        "nvcf.function_version_id": str(session.function_version_id),
        "session.status": session.status,
    })
    session_end.add(1, metric_attrs)
    session_duration.record(session.duration, metric_attrs)


def attributes(attrs: Mapping) -> dict:
    return {
        key: value
        for key, value in attrs.items()
        if value is not None
    }