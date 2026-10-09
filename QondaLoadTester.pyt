# -*- coding: utf-8 -*-
"""
Qonda Load Tester - ArcGIS Pro Python toolbox.

Generates a .websurge test file for Qonda Load Tester from the current map:
  - Test area: the active map view's extent, or the polygons of a layer named
    "extent" when one is present (honours selection / definition query).
  - Services: every MapServer / FeatureServer layer in the map. Non-service
    layers and basemap layers are ignored.

Output matches TestGenerator.cs / SpatialCalc.cs so the file loads with
"Load Existing Test" and the report heat map works.
"""

import json
import math
import os
import random
import re
import ssl
import urllib.parse
import urllib.request

import arcpy


EXTENT_LAYER_NAME = "extent"
DEFAULT_SCALES    = "250000,100000,50000,25000,10000,5000,2500,1000,500,250"
EXPORT_SIZE       = "1000,1000"   # Same as TestGenerator.cs
SEPARATOR         = "------------------------------------------------------------------"

# https://host/server/rest/services/Folder/Name/MapServer/3  ->  base, service path, layer id
SERVICE_URL_RE = re.compile(
    r"^(?P<base>https?://[^?#]+?)/rest/services/"
    r"(?P<svc>[^?#]+?/(?:MapServer|FeatureServer))"
    r"(?:/(?P<id>\d+))?(?:[/?#]|$)",
    re.IGNORECASE)


class Toolbox(object):
    def __init__(self):
        self.label = "Qonda Load Tester"
        self.alias = "qonda"
        self.tools = [GenerateTest]


class GenerateTest(object):
    def __init__(self):
        self.label = "Generate Load Test From Map"
        self.description = ("Creates a Qonda Load Tester .websurge test file from the "
                            "current map's extent (or an 'extent' polygon layer) and "
                            "the map/feature service layers in the map.")
        self.canRunInBackground = False

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------
    def getParameterInfo(self):
        out_file = arcpy.Parameter(
            displayName="Output Test File", name="out_file",
            datatype="DEFile", parameterType="Required", direction="Output")
        out_file.filter.list = ["websurge"]
        try:
            home = arcpy.mp.ArcGISProject("CURRENT").homeFolder
            out_file.value = os.path.join(home, "Test.websurge")
        except Exception:
            pass

        scales = arcpy.Parameter(
            displayName="Scales (comma separated)", name="scales",
            datatype="GPString", parameterType="Required", direction="Input")
        scales.value = DEFAULT_SCALES

        points = arcpy.Parameter(
            displayName="Number of Test Points", name="points",
            datatype="GPLong", parameterType="Required", direction="Input")
        points.value = 50

        use_extent = arcpy.Parameter(
            displayName="Generate points within the '{0}' polygon layer (if present)".format(EXTENT_LAYER_NAME),
            name="use_extent_layer",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        use_extent.value = True

        queries = arcpy.Parameter(
            displayName="Include layer query requests", name="include_queries",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        queries.value = True

        exports = arcpy.Parameter(
            displayName="Include map export requests (MapServer only)", name="include_exports",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        exports.value = True

        randomise = arcpy.Parameter(
            displayName="Randomise request order", name="randomise",
            datatype="GPBoolean", parameterType="Optional", direction="Input")
        randomise.value = True

        path_style = arcpy.Parameter(
            displayName="Request Paths", name="path_style",
            datatype="GPString", parameterType="Optional", direction="Input")
        path_style.filter.type = "ValueList"
        path_style.filter.list = ["Relative", "Absolute"]
        path_style.value = "Relative"

        width = arcpy.Parameter(
            displayName="Image Width (px)", name="image_width",
            datatype="GPDouble", parameterType="Optional", direction="Input", category="Advanced")
        width.value = 1920

        height = arcpy.Parameter(
            displayName="Image Height (px)", name="image_height",
            datatype="GPDouble", parameterType="Optional", direction="Input", category="Advanced")
        height.value = 1080

        dpi = arcpy.Parameter(
            displayName="DPI", name="dpi",
            datatype="GPDouble", parameterType="Optional", direction="Input", category="Advanced")
        dpi.value = 96

        companions = arcpy.Parameter(
            displayName="Also write Centroids.csv, Extents.csv and GeoJSON", name="write_companions",
            datatype="GPBoolean", parameterType="Optional", direction="Input", category="Advanced")
        companions.value = False

        return [out_file, scales, points, use_extent, queries, exports, randomise,
                path_style, width, height, dpi, companions]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        p = {x.name: x for x in parameters}

        if p["scales"].value:
            try:
                if not _parse_scales(p["scales"].valueAsText):
                    p["scales"].setErrorMessage("Enter at least one scale.")
            except ValueError:
                p["scales"].setErrorMessage("Scales must be positive whole numbers separated by commas.")

        if p["points"].value is not None and p["points"].value < 1:
            p["points"].setErrorMessage("Must be at least 1.")

        if not p["include_queries"].value and not p["include_exports"].value:
            p["include_exports"].setErrorMessage("Include queries, exports, or both.")

        for name in ("image_width", "image_height", "dpi"):
            if p[name].value is not None and p[name].value <= 0:
                p[name].setErrorMessage("Must be greater than 0.")
        return

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------
    def execute(self, parameters, messages):
        p = {x.name: x for x in parameters}
        out_file        = p["out_file"].valueAsText
        scales          = _parse_scales(p["scales"].valueAsText)
        num_points      = int(p["points"].value)
        use_extent      = bool(p["use_extent_layer"].value)
        include_queries = bool(p["include_queries"].value)
        include_exports = bool(p["include_exports"].value)
        randomise       = bool(p["randomise"].value)
        absolute        = (p["path_style"].valueAsText or "Relative") == "Absolute"
        img_w           = float(p["image_width"].value or 1920)
        img_h           = float(p["image_height"].value or 1080)
        dpi             = float(p["dpi"].value or 96)
        companions      = bool(p["write_companions"].value)

        aprx = arcpy.mp.ArcGISProject("CURRENT")
        m, view_extent = _active_map_and_extent(aprx)

        sr = m.spatialReference
        wkid = sr.factoryCode
        if not wkid:
            raise arcpy.ExecuteError("The map's coordinate system has no WKID; "
                                     "set the map to a standard coordinate system.")
        is_geographic = sr.type == "Geographic"
        meters_per_unit = 1.0 if is_geographic else (sr.metersPerUnit or 1.0)
        arcpy.AddMessage("Map: {0}  (SR {1} - {2})".format(m.name, wkid, sr.name))

        # -- Services ---------------------------------------------------
        services = _collect_services(m)
        if not services:
            raise arcpy.ExecuteError("No MapServer or FeatureServer layers found in the map.")

        bases = sorted({s["base"] for s in services.values()})
        if not absolute and len(bases) > 1:
            arcpy.AddWarning("Services come from more than one server ({0}). Relative paths "
                             "only work against one server URL - consider Absolute paths."
                             .format(", ".join(bases)))

        _fill_missing_layer_ids(services)

        # -- Test points ------------------------------------------------
        polygon = _extent_polygon(m, sr) if use_extent else None
        if polygon is not None:
            arcpy.AddMessage("Generating {0} points within the '{1}' layer".format(
                num_points, EXTENT_LAYER_NAME))
            pts = _random_points_in_polygon(polygon, num_points)
        else:
            if use_extent:
                arcpy.AddMessage("No '{0}' polygon layer found - using the map extent".format(
                    EXTENT_LAYER_NAME))
            e = view_extent
            arcpy.AddMessage("Extent: {0:.4f}, {1:.4f}, {2:.4f}, {3:.4f}".format(
                e.XMin, e.YMin, e.XMax, e.YMax))
            pts = [(random.uniform(e.XMin, e.XMax), random.uniform(e.YMin, e.YMax))
                   for _ in range(num_points)]

        # -- Extents per point per scale (SpatialCalc.GenerateExtents) ---
        round_to = 4 if is_geographic else 1
        rows = []
        row_id = 1
        for cx, cy in pts:
            for scale in scales:
                b = _calc_extent(is_geographic, meters_per_unit, scale, cx, cy, dpi, img_w, img_h)
                b = [round(v, round_to) for v in b]
                rows.append({"id": row_id, "bbox": b, "scale": scale, "x": cx, "y": cy})
                row_id += 1

        bbox_fmt = "{0:.%df}" % round_to

        # -- Requests (TestGenerator.GenerateTestFile) -------------------
        requests = []
        for svc_path in sorted(services):
            svc = services[svc_path]
            layer_ids = sorted(svc["ids"])
            is_map_server = svc_path.lower().endswith("/mapserver")
            prefix = (svc["base"] + "/rest/services/" + svc_path) if absolute else svc_path
            before = len(requests)

            for row in rows:
                bbox = ",".join(bbox_fmt.format(v) for v in row["bbox"])
                b = row["bbox"]
                tag = "|{0}|{1:.6f}|{2:.6f}|{3}".format(
                    row["id"], (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0, row["scale"])

                for lid in layer_ids:
                    if include_queries:
                        requests.append(_block(
                            "{0}/{1}/query".format(prefix, lid),
                            "{0}/{1}/query{2}".format(svc_path, lid, tag),
                            _query_body(bbox, wkid)))
                    if include_exports and is_map_server:
                        requests.append(_block(
                            prefix + "/export",
                            "{0}/export ({1}){2}".format(svc_path, lid, tag),
                            _export_body(bbox, wkid, str(lid))))

                if include_exports and is_map_server and (len(layer_ids) > 1 or not layer_ids):
                    # Combined export; with no known layer ids, let the service draw its defaults.
                    requests.append(_block(
                        prefix + "/export",
                        "{0}/export (all){1}".format(svc_path, tag),
                        _export_body(bbox, wkid, ",".join(str(i) for i in layer_ids))))

            arcpy.AddMessage("  {0}: layers [{1}] -> {2} requests".format(
                svc_path, ",".join(str(i) for i in layer_ids) or "default", len(requests) - before))

        if not requests:
            raise arcpy.ExecuteError("No requests generated - check the query/export options.")

        if randomise:
            random.shuffle(requests)

        with open(out_file, "w", encoding="utf-8", newline="") as f:
            f.write("".join(requests) + "\r\n")

        arcpy.AddMessage("Wrote {0} requests ({1} points x {2} scales) to {3}".format(
            len(requests), len(pts), len(scales), out_file))

        if companions:
            _write_companions(out_file, pts, rows, bbox_fmt, img_w, img_h,
                              "degrees" if is_geographic else "meters", wkid)

        if not any(s.lower().endswith("/mapserver") for s in services) or not include_exports:
            arcpy.AddWarning("The test has no export requests, so the load tester can't detect "
                             "the SRID from the file. Set the Input Extent's SRID to {0} in the "
                             "app so the report heat map lines up.".format(wkid))
        return

    def postExecute(self, parameters):
        return


# ----------------------------------------------------------------------
# Map helpers
# ----------------------------------------------------------------------
def _active_map_and_extent(aprx):
    view = aprx.activeView
    if view is not None and hasattr(view, "camera") and hasattr(view, "map"):
        return view.map, view.camera.getExtent()

    m = aprx.activeMap
    if m is None:
        raise arcpy.ExecuteError("Open a map view before running this tool.")
    arcpy.AddWarning("No active map view - using the map's default extent.")
    return m, m.defaultCamera.getExtent()


def _layer_urls(lyr):
    """All URL-ish strings a layer exposes; any of them may identify the service."""
    urls = []
    try:
        if lyr.supports("DATASOURCE"):
            urls.append(lyr.dataSource)
    except Exception:
        pass
    try:
        if lyr.supports("SERVICEPROPERTIES"):
            urls.append(lyr.serviceProperties.get("URL"))
    except Exception:
        pass
    try:
        cp = lyr.connectionProperties or {}
        info = cp.get("connection_info") or {}
        url = info.get("url") or info.get("URL")
        if url:
            urls.append(url)
            ds = cp.get("dataset")
            if ds is not None and str(ds).isdigit():
                urls.append(url.rstrip("/") + "/" + str(ds))
    except Exception:
        pass
    return [u for u in urls if u]


def _collect_services(m):
    """{service path: {"base": server url, "ids": set(layer ids)}} for service layers in the map."""
    services = {}
    for lyr in m.listLayers():
        try:
            if getattr(lyr, "isBasemapLayer", False):
                continue
        except Exception:
            pass
        matches = [SERVICE_URL_RE.match(u.strip()) for u in _layer_urls(lyr)]
        matches = [mt for mt in matches if mt]
        if not matches:
            continue
        # Prefer a URL that names the layer id over the bare service URL
        match = next((mt for mt in matches if mt.group("id") is not None), matches[0])
        svc = services.setdefault(match.group("svc"),
                                  {"base": match.group("base"), "ids": set()})
        if match.group("id") is not None:
            svc["ids"].add(int(match.group("id")))
    return services


def _fill_missing_layer_ids(services):
    """Fallback for map image layers that don't expose sublayer ids - ask the
    service, using the Pro sign-in token for secured services."""
    if all(svc["ids"] for svc in services.values()):
        return
    token, referer = "", None
    try:
        info = arcpy.GetSigninToken()
        if info:
            token, referer = info.get("token", ""), info.get("referer")
    except Exception:
        pass

    for svc_path, svc in services.items():
        if svc["ids"]:
            continue
        url = svc["base"] + "/rest/services/" + svc_path
        data = {"f": "json"}
        if token:
            data["token"] = token
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode())
            if referer:
                req.add_header("Referer", referer)
            with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as r:
                js = json.loads(r.read().decode("utf-8"))
            if "error" in js:
                raise Exception(js["error"].get("message", "error"))
            svc["ids"].update(int(l["id"]) for l in js.get("layers", []) if "id" in l)
        except Exception as ex:
            arcpy.AddWarning("Could not list layers for {0} ({1}). Only a default "
                             "export will be tested for it.".format(svc_path, ex))


def _extent_polygon(m, sr):
    """Union of all (selected / definition-queried) polygons in the 'extent' layer, in map SR."""
    for lyr in m.listLayers():
        if lyr.name.lower() != EXTENT_LAYER_NAME or not lyr.isFeatureLayer:
            continue
        try:
            if arcpy.Describe(lyr).shapeType != "Polygon":
                continue
        except Exception:
            continue
        geom = None
        with arcpy.da.SearchCursor(lyr, ["SHAPE@"], spatial_reference=sr) as cur:
            for (shape,) in cur:
                if shape is None or shape.area <= 0:
                    continue
                geom = shape if geom is None else geom.union(shape)
        if geom is None:
            arcpy.AddWarning("The '{0}' layer has no polygons to use.".format(EXTENT_LAYER_NAME))
        return geom
    return None


def _random_points_in_polygon(polygon, count):
    e = polygon.extent
    sr = polygon.spatialReference
    pts = []
    attempts = 0
    max_attempts = max(10000, count * 1000)
    while len(pts) < count and attempts < max_attempts:
        attempts += 1
        x = random.uniform(e.XMin, e.XMax)
        y = random.uniform(e.YMin, e.YMax)
        if polygon.contains(arcpy.PointGeometry(arcpy.Point(x, y), sr)):
            pts.append((x, y))
    if len(pts) < count:
        arcpy.AddWarning("Only placed {0} of {1} points inside the polygon.".format(len(pts), count))
    return pts


# ----------------------------------------------------------------------
# Geometry (port of SpatialCalc.cs)
# ----------------------------------------------------------------------
def _calc_extent(is_geographic, meters_per_unit, scale, x, y, dpi, width, height):
    w_in = width / dpi
    h_in = height / dpi
    if is_geographic:
        inch_in_deg = 0.0254 / _lat_length(abs(y))
        w_unit = w_in * inch_in_deg
        h_unit = h_in * inch_in_deg
    else:
        w_unit = w_in * 0.0254 / meters_per_unit
        h_unit = h_in * 0.0254 / meters_per_unit
    dx = w_unit * scale / 2.0
    dy = h_unit * scale / 2.0
    return [x - dx, y - dy, x + dx, y + dy]


def _lat_length(lat):
    r = math.radians(lat)
    return (111132.92 - 559.82 * math.cos(2 * r)
            + 1.175 * math.cos(4 * r) - 0.0023 * math.cos(6 * r))


def _parse_scales(text):
    scales = []
    for s in (text or "").split(","):
        s = s.strip()
        if not s:
            continue
        v = int(float(s))
        if v <= 0:
            raise ValueError(s)
        scales.append(v)
    return scales


# ----------------------------------------------------------------------
# WebSurge output (port of TestGenerator.cs)
# ----------------------------------------------------------------------
def _query_body(bbox, wkid):
    return ("geometryType=esriGeometryEnvelope&geometry={0}&inSR={1}"
            "&outFields=*&returnGeometry=true&f=json").format(bbox, wkid)


def _export_body(bbox, wkid, layers):
    body = "bbox={0}&bboxSR={1}&size={2}&dpi=96".format(bbox, wkid, EXPORT_SIZE)
    if layers:
        body += "&layers=show:" + layers
    return body + "&f=image"


def _block(path, name, body):
    return ("POST " + path + " HTTP/2.0\r\n"
            "Websurge-Request-Name: " + name + "\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "\r\n" + body + "\r\n"
            "\r\n" + SEPARATOR + "\r\n")


def _write_companions(out_file, pts, rows, bbox_fmt, img_w, img_h, units, wkid):
    folder = os.path.dirname(out_file)
    stem = os.path.splitext(os.path.basename(out_file))[0]

    with open(os.path.join(folder, stem + "_Centroids.csv"), "w", encoding="utf-8") as f:
        for x, y in pts:
            f.write("{0!r},{1!r}\n".format(x, y))

    features = []
    with open(os.path.join(folder, stem + "_Extents.csv"), "w", encoding="utf-8") as f:
        f.write("id,bbox,width,height,mapUnits,sr,scale\n")
        for r in rows:
            b = r["bbox"]
            f.write('{0},"{1}",{2},{3},{4},{5},{6}\n'.format(
                r["id"], ",".join(bbox_fmt.format(v) for v in b),
                int(round(img_w)), int(round(img_h)), units, wkid, r["scale"]))
            features.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[
                    [b[0], b[1]], [b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]]]},
                "properties": {"id": r["id"], "scale": r["scale"], "srid": wkid,
                               "x": r["x"], "y": r["y"]}})

    with open(os.path.join(folder, stem + "_GeoJSON.json"), "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, indent=2)

    arcpy.AddMessage("Wrote {0}_Centroids.csv, {0}_Extents.csv and {0}_GeoJSON.json".format(stem))
