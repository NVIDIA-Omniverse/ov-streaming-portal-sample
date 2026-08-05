# Omniverse Streaming Portal Sample Changelog

## Version 1.6.1 - August 2026

- Send NVCF reconnect window header and document 1800s maximum limit
- End the identity provider session when users log out so they are asked to authenticate again instead of being signed back in, and redirect them to the home page
- Ask users on log out whether to keep their streaming sessions running or end them all, reporting the outcome of every session that is ended
  - Note: System administrators can only end their own sessions
- Added an `own` query parameter to `GET /sessions/` that restricts the result to the sessions of the current user, which system administrators can use to exclude sessions of other users
- Report sessions that users end themselves as terminated by the user instead of by a system administrator
- Update FastAPI, Starlette, cryptography, and mcp to address known CVEs
- Skip OTLP export when non-configured and omit `None` metric attributes

## Version 1.6.0 - July 2026

- Renamed to Omniverse Streaming Portal Sample to reflect its expanded compatibility with Omniverse Streaming Self-Hosted
- Include sample NavVis integration files
- Encode page name in the sidebar link to fix pages with plus (+) sign character
- Show packet loss in stream latency indicator popup
- Update `@nvidia/ov-web-rtc` (previously known as `@nvidia/web-streaming-library`) to version 6.6.0
- Added functionality (checkbox) for a stream to automatically fit to the current browser dimensions
- Add fit-to-browser resize toggle
- Add recent packet loss metric to stream connection stats

## Version 1.5.1 - June 2026

- Added `EXPIRED` and `FAILED` statuses to track how streaming sessions ended
- Record the last error reported for a streaming session and allow users to copy it from the `FAILED` badge
- Added an MCP server exposing Sample Portal application tools (publish, remove, and inspect streaming apps) with an OAuth broker implementing PKCE and dynamic client registration for MCP clients, and a database migration for OAuth state
- Added agent skills for publishing, removing, and checking streaming apps, checking NVCF functions, and diagnosing streaming issues
- Added streaming issue documentation covering `ov-web-rtc` error codes, build/package failures, NVCF deployment(s), portal registration(s), and Sample Portal UI troubleshooting

## Version 1.5.0 - May 2026

- Support USD Storage API authentication - allow users to add new connections to USD Storage API deployments during streaming sessions and automatically refresh USD Storage API sessions
- Fixed an issue where users could have been redirected to the identity provider during a streaming session
- Allow users to request a stream resolution before starting a streaming session
- Hide streaming latency indicator when stream is stopped or terminated
- Fixed an issue where application cards were not automatically updated to reflect a `degraded` function status
- Support ordering sessions by ID, app, user, start date, end date and duration on the session list page
- Purge stale session data from the database after configured retention
- Added an about page to display the portal deployment settings
- Display deployment details on the application info page
- Update `@nvidia/ov-web-rtc` (previously known as `@nvidia/web-streaming-library`) to version 6.2.2
- Display a notification when the browser does not support the HEVC/H.265 codec required for 4K streaming
- Fit the stream resolution to the browser window to avoid letterboxing in non-16:9 layouts (requires a resize-enabled streaming application)
- Emit session end metrics when idle sessions are terminated
- Validate JWT iss claim to prevent cross-tenant token acceptance
- Add Page info to application deployment details page

## Version 1.4.2 - March 2026

- Display degraded applications on the main page with disabled state, warning indicator, and per-version status tracking
- Added `GET /users/me` endpoint that returns information about the currently authenticated user
- Allow system administrators to copy the NVCF Request ID of a session from the sessions page
- Reset the end date if a user reconnects to a streaming session
- Added OpenAPI tags for all backend endpoints

## Version 1.4.1 - January 2026

- Hide latency indicator if latency data is not available
- Update `GET /api/pages/` endpoint to return pages submitted only to `PUT /api/apps/`
- Display applications with degrading status in addition to active applications
- Fixed an issue where session timeout checks could be calculated incorrectly if the host/system runs using a non-UTC timezone
- Report OpenTelemetry metrics for the following:
	- Number of GPUs assigned (quota) to the NGC organization
	- Number of GPUs currently assigned to active NVCF functions
- Set application name as the browser tab title when stream starts
- Force the focus of the viewport when stream starts
- Update `@nvidia/omniverse-webrtc-streaming-library` to 5.17.0

## Version 1.4.0 - November 2025

- Added 'Deep Link' functionality for sharing a scene and other attributes (e.g., camera position) with other streaming users
- Added configuration for changing the home page title
- Update fastapi and starlette dependencies for Python backend
- Add "DEGRADING" status for NVCF streaming functions
- Remove `session.id` and `session.duration.seconds` attributes to reduce metric cardinality
- Invalidate NVCF function status cache when applications are added or removed from the portal
- Fixed an error that could occur on the home page if displayed applications used non-semantic versioning
- Changed the default aggregation temporality for OpenTelemetry metrics
- Added latency indicator for streaming sessions\*

**\*Note:** There may be a slight delay on the latency indicator reporting proper status while users are joining sessions. *(This will be enhanced in a future update.)*


## Version 1.3.1 - October 2025

- Fixed an issue where multiple tabs could try to refresh the authentication session simultaneously
- Added retries for establishing a new session and connecting to a running session
- Minor UI changes and added context to status and error messages for clarity
- Add "DEGRADED" status for NVCF streaming functions
- Update `@nvidia/omniverse-webrtc-streaming-library` to 5.15.5


## Version 1.3.0 - October 2025

- Fixed an issue where WebSocket endpoints couldn't be used with large headers
- Check Nucleus authentication before starting a streaming session
- Added the `page` field for grouping applications on different pages with a sidebar
- Added `/api/pages/` endpoint that returns the page order for the sidebar
- Removed unused `image` field for applications
- Update Python container to 3.13


## Version 1.2.0 - August 2025

 - Use OpenID Connect Discovery for authentication*
 - Adds the following OTel session metrics:
	 - Total Number of Active Sessions
	 - Start Session
	 - End Session
	 - Session Duration
- Adds the following attributes to session metrics:
	 - Session ID
	 - Session User Name
	 - Session Application Name
	 - NVCF Function ID
	 - NVCF Function Version
- Fixed an issue where the portal could display an error on all pages if it failed to refresh the session
- Support for configuring the header size limit
- Add a User column to the session list modal in the admin view of the session manager
- Hide the reconnect button for sessions of other users in the admin view of the session manager
- Add **media_server** and **media_port** for streaming functions
- Stop abandoned sessions (e.g., user created a session but chose not to connect or failed to connect)
- Disable Sessions link when users are on the Sessions page


**NOTE:**  This update to the Portal Sample introduces a change when integrating with an IDP. The Portal administrator needs to configure the `config.auth.metadataUri` with the IdP URI that points to OpenID Connect Discovery. This URL returns all necessary information for the Portal, including the jwks URI and userinfo URI.

This update adds greater support for IDPs, including Microsoft EntraID, and further simplifies the implementation.



## Version 1.1.0 - July 2025

- Fixed incorrect output for internal termination errors
- Specify path for cookies deleted on session termination
- Update python dependencies
- Check if a session still exists before attempting to reconnect
- Add a comment how to enable Azure PE/PLS streaming
- Allow users to view and terminate their own sessions within the Portal session manager
- Allow an app to be launched multiple times from the Portal with resume
- Increase timeouts for popup notifications
- Added a page for displaying application detailed information
- Display minutes for session duration
- Pass OpenID Connect access tokens to the stream with Nucleus-Token header
- Add a script to create an NVCF function with DDCS and Content Cache enabled
- Display a warning before the session ends
- Increase line-height for application title
- Implement API keys to allow calling the API from other services or CI
- Fixed an issue where cookies were not cleared correctly after session termination


## Version 1.0.0 - June 2025

- Intitial release
