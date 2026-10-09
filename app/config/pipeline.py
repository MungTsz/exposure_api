"""
Tunable thresholds for the ETL pipeline (app/etl). These shape the
pipeline's output, so they are the values worth sweeping while checking
results against data/samples/ in notebooks/pipeline_walkthrough.ipynb.
Change values here, not the logic in app/etl.

Thresholds that decide which io type a stay gets (e.g. how long a stay
must last to count as indoor) live in io_types.py instead.
"""

# --- Spike removal (app/etl/spikes.py) ---
SPIKE_ANGLE_DEG = 20.0           # angle at B below this = suspected spike
SPIKE_TIME_WINDOW_SECONDS = 180  # only flag as a spike if A->C happened this fast

# --- Grouping pings into stays (app/etl/stays.py) ---
# Degree-based "same location" tolerance for GPS drift, deliberately used
# instead of a haversine distance so the check is a plain lat/lng delta
# comparison. 0.0001 deg of lat/lng is ~11.1m, so ~0.00018 deg is ~20m - loose
# enough to absorb typical indoor GPS drift (consumer GPS horizontal accuracy
# is commonly 5-20m, worse indoors from multipath), tight enough not to merge
# genuinely separate stops. Applied as a flat box on both lat and lng, with
# no per-latitude correction for longitude compression.
STAY_DEGREE_THRESHOLD = 0.00018

# A ping only counts as part of the same stay if its own reported speed
# (when the device provides one - many raw pings don't) is below this.
# ~1.0 m/s (~3.6 km/h) is a brisk-walk cutoff: comfortably separates indoor
# puttering (getting up, pacing a room) from real outdoor travel (walking
# commute is usually 1.2 m/s+, any transit mode faster still), without
# rejecting legitimate slow indoor movement. A missing speed reading doesn't
# block a ping from joining the stay - only a reported *fast* speed does.
SPEED_THRESHOLD_MPS = 1.0

# --- Route simplification (app/etl/routes.py) ---
# While moving, the device logs ~1 ping/second and nearly every ping becomes
# its own 1-second stay (it leaves the stay box immediately), which floods
# expo_calx with rows and the app's map with points. simplify_routes thins
# each continuous route before scoring: starting from the last kept point,
# every following point is dropped while it is still inside a
# ROUTE_POINT_DEGREE_THRESHOLD lat/lng box of that point AND less than
# ROUTE_POINT_MAX_SECONDS have passed since it. The first point breaking
# either condition is kept and becomes the new reference, so points land
# roughly evenly along the route. Dropped points' time is folded into the
# preceding kept point, so total time is preserved exactly.
#
# 0.0027 deg is ~300m of lat (~280m of lng at HK latitude) - a flat box like
# STAY_DEGREE_THRESHOLD, not haversine. 300s means walking (~1.3 m/s covers
# ~390m in 5 min) and anything faster is distance-triggered (~1 point per
# 300m), while a pause or slow stroll still gets a point at least every
# 5 min, which caps how much time one point's position stands in for.
# On data/samples/20260830.geojson: 238 points, median spacing ~304m, 7
# expo_calx calls (the earlier 0.0009/90s gave ~560 points, ~110m, 15 calls).
# Raise both values for a sparser map, lower them for a denser one.
ROUTE_POINT_DEGREE_THRESHOLD = 0.0027
ROUTE_POINT_MAX_SECONDS = 300.0

# A route breaks (never simplified across) when one stay lasts longer than
# MAX_GAP_SECONDS - a stay's end is the next ping's time, so a long stay in
# a moving route means a GPS dropout - or when two consecutive stays'
# points are more than MAX_GAP_DEGREES apart (e.g. GPS drops in a tunnel and
# resumes far away). Both endpoints of a real gap therefore survive as their
# own points.
#
# The jump is measured as |dlat| + |dlng| in degrees (Manhattan distance, a
# flat approximation like STAY_DEGREE_THRESHOLD, no trig). 0.0018 deg is
# ~200m due north/south and ~185m due east/west at HK latitude; on a
# diagonal L1 overestimates by up to sqrt(2), so the cutoff there is ~140m.
# 0.0018 deg / 120s sit comfortably above the typical few-meters/few-seconds
# gap between consecutive pings during normal continuous tracking in the
# sample data (data/samples/*.geojson), while well below the
# multi-hundred-meter/multi-minute jumps a genuine dropout produces.
MAX_GAP_DEGREES = 0.0018
MAX_GAP_SECONDS = 120.0
