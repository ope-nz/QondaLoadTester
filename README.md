# Qonda Load Tester

A Windows desktop tool for load testing ArcGIS Server REST services. It generates test request files, executes them with configurable concurrency, and produces an HTML performance report.

---

## Requirements

- Windows with .NET Framework 4.5 or later
- ArcGIS Server with REST API access

---

## Getting Started

### 1. Authentication

Enter the **Server URL** (e.g. `https://myserver/server`), **Username**, and **Password**, then click **Login**. A token will appear in the Token field. The **Run Test** button remains disabled until a token is present.

> The token is not saved between sessions for security reasons.

---

## Modes

Use the **Mode** radio buttons to choose how to proceed.

### Create New Test

Walks through generating a test file from scratch using live service discovery.

**Step 1 — Services**

Click **List Services** to populate the list with all MapServer and FeatureServer services. Select one or more services to test. Clicking a service automatically retrieves its spatial extent.

**Step 2 — Spatial Config**

- **Input Extent**: the bounding box to test within, formatted as `xmin,ymin,xmax,ymax,SRID` (e.g. `170,-46,178,-34,4326`). Auto-populated when you click a service.
- **Scales**: comma-separated map scales to test at (e.g. `250000,50000,10000,1000`).

**Step 3 — Point Generation**

- **Test Points**: number of random locations to generate within the extent.
- Click **Generate Points** to create `Centroids.csv` in the app folder.

**Step 4 — Extent Generation**

Click **Generate Extents** to calculate map bounding boxes for each centroid at each scale. Produces `Extents.csv` and `GeoJSON.json`.

**Step 5 — Generate Test**

Click **Generate Test** to build `Test.websurge`, a request file containing one HTTP POST per service/layer/scale/location combination. The test file path appears in the Test Execution section.

### Load Existing Test

Hides the generation steps and shows a **Browse** button. Use this to re-run a previously generated `.websurge` test file without regenerating it.

---

## Test Execution

Configure the run settings and click **Run Test**.

| Setting | Description |
|---|---|
| **Test File** | Path to the `.websurge` file to run |
| **Threads** | Number of concurrent requests |
| **Duration (s)** | How long to run the test |
| **Randomise** | Shuffle the request order each cycle |
| **Ramp Threads** | Start at 1 thread and ramp up to the configured thread count over the first 33% of the test duration |
| **Warmup** | Seconds of warm-up requests to send before the timed test begins (not logged) |

When the test completes, the report is generated automatically and opened in the browser.

> Settings are saved between sessions (except the token).

---

## Reading the Report

### KPI Summary

| Metric | Description |
|---|---|
| **Req / sec** | Total requests per second (including errors) |
| **Avg TPS** | Successful transactions per second (errors excluded) |
| **Avg TTFB** | Average time to first byte (server processing time) |
| **Avg Download** | Average time to download the response body |
| **Avg Total** | Average end-to-end response time (TTFB + download) |
| **p50 / p95 / p99** | Percentile response times — 95% of requests completed within p95 |
| **Min / Max** | Fastest and slowest individual requests |
| **Data** | Total response data received |
| **Throughput** | Average data rate in KB/s |
| **Errors** | Total failed requests (HTTP 4xx/5xx and network errors) |
| **Error Rate** | Errors as a percentage of total requests |

---

### Charts

#### Concurrent Requests Over Duration

Stepped line chart showing how many threads were active at each point in time. Useful for verifying that ramp-up behaved as expected.

#### Response Time Over Duration

Scatter chart of individual response times (ms) plotted against elapsed test time (s).

- **Blue dots**: normal requests (below p95)
- **Red dots**: outliers (above p95 threshold)

Patterns to look for:
- A gradual upward trend suggests the server is struggling under sustained load.
- Sudden spikes may indicate garbage collection pauses or external interference.
- Consistent flat lines suggest the service is caching responses.

#### Requests Over Duration

Scatter chart showing throughput per second over time.

- **Green dots**: successful requests per second
- **Red dots**: errors per second

A drop in green dots accompanied by rising red dots indicates the server is failing under load.

#### Response Time Distribution

Histogram showing how response times are distributed across the full range. A narrow peak indicates consistent performance. A long right tail or bimodal distribution suggests some requests are being handled differently (e.g. cache misses, slow layers).

#### Status Codes

Doughnut chart showing the breakdown of HTTP response codes. Ideally all 200. Any 4xx or 5xx codes warrant investigation.

#### Avg TTFB vs Download by Endpoint

Stacked horizontal bar chart per endpoint. The green segment is average time to first byte (server processing), the blue segment is average download time. Hover for the combined total.

- A long TTFB indicates server-side processing is the bottleneck.
- A long download suggests large response payloads.

#### Avg & p95 by Endpoint

Compares average vs p95 response time per endpoint. A large gap between the two means some requests are occasionally much slower than typical, worth investigating for slow layers or scale-dependent performance.

#### Request Count by Endpoint

Shows how many requests were sent to each endpoint during the test. Useful for confirming the test file covers services evenly.

#### Response Time Heat Map

Only shown when the test was generated with spatial coordinates. Dots are placed at each test location, coloured from green (fast) to red (slow). Use the **Scale** dropdown to filter by map scale.

Hover over a dot to see the average response time and request count for that location. Clusters of red dots indicate geographic hotspots where the service is consistently slow (e.g. dense data areas, tile boundaries).

---

## Output Files

All output files are written to the application folder.

| File | Description |
|---|---|
| `Test.websurge` | Generated test request file |
| `Centroids.csv` | Random test point locations |
| `Extents.csv` | Bounding boxes per point/scale combination |
| `GeoJSON.json` | Test extents as GeoJSON for inspection in a GIS |
| `results-1.csv` | Raw test results (one row per request) |
| `results-1.html` | HTML performance report |
| `config.json` | Saved settings |
| `app.log` | Application log for troubleshooting |
