"""
Single home for every tunable threshold that shapes the ETL pipeline's
*output* - the parameters worth sweeping while eyeballing results against
the sample data in testing/data/ via testing/testing.ipynb. etl.py imports
its constants from here; adjust values here rather than the logic itself.

Deliberately excluded (not output-shaping / not worth sweeping):
  - etl.py's HK_OFFSET (a fixed timezone fact, not a threshold) and
    _API_IO_LABEL (a fixed third-party API vocabulary mapping) stay in
    etl.py itself.
  - praise_client.py's MAX_ROWS_PER_CALL is an expo_calx API-integration
    concern, not ETL/merge output tuning.
"""

# --- etl.py: spike filter tuning ---
SPIKE_ANGLE_DEG = 20.0          # angle at B below this = suspected spike
SPIKE_TIME_WINDOW_SECONDS = 180  # only flag as a spike if A->C happened this fast

# --- etl.py: indoor/outdoor dwell tuning ---
# NOTE on the "waiting for the bus" problem: a genuinely-indoor ~20min stop
# and an outdoor ~20min transit wait are, by design, indistinguishable by
# dwell duration/box/speed alone - raising DWELL_INDOOR_MINUTES (and/or
# tightening DWELL_DEGREE_THRESHOLD/SPEED_THRESHOLD_MPS) is a tuning
# tradeoff to reduce false-Indoor bus-wait classifications, not a structural
# fix; it will also delay genuinely-indoor classification by the same
# amount. See etl.py's find_dwell_runs.
DWELL_INDOOR_MINUTES = 20       # a stationary run at least this long = indoor

# Degree-based "same location" tolerance for GPS drift, deliberately used
# instead of a haversine distance so the check is a plain lat/lng delta
# comparison. 0.0001 deg of lat/lng is ~11.1m, so ~0.00018 deg is ~20m - loose
# enough to absorb typical indoor GPS drift (consumer GPS horizontal accuracy
# is commonly 5-20m, worse indoors from multipath), tight enough not to merge
# genuinely separate stops. Applied as a flat box on both lat and lng, with
# no per-latitude correction for longitude compression.
DWELL_DEGREE_THRESHOLD = 0.00018

# A point only counts as part of the same stationary run if its own reported
# speed (when the device provides one - many raw pings don't) is below this.
# ~1.0 m/s (~3.6 km/h) is a brisk-walk cutoff: comfortably separates indoor
# puttering (getting up, pacing a room) from real outdoor travel (walking
# commute is usually 1.2 m/s+, any transit mode faster still), without
# rejecting legitimate slow indoor movement. A missing speed reading doesn't
# block a point from joining the run - only a reported *fast* speed does.
SPEED_THRESHOLD_MPS = 1.0

# --- etl.py: outdoor trail simplification ---
# While moving, the device logs ~1 ping/second and nearly every ping becomes
# its own 1-second Outdoor run (it leaves the dwell box immediately), which
# floods expo_calx with rows and the app's map with dots.
# simplify_outdoor_trails thins each continuous Outdoor trail before
# scoring: starting from the last kept point, every following point is
# dropped while it is still inside a TRAIL_DOT_DEGREE_THRESHOLD lat/lng box
# of that point AND less than TRAIL_DOT_MAX_SECONDS have passed since it.
# The first point breaking either condition is kept and becomes the new
# reference, so dots land roughly evenly along the track. Dropped points'
# time is folded into the preceding kept point, so total time is preserved
# exactly.
#
# 0.0027 deg is ~300m of lat (~280m of lng at HK latitude) - a flat box like
# DWELL_DEGREE_THRESHOLD, not haversine. 300s means walking (~1.3 m/s covers
# ~390m in 5 min) and anything faster is distance-triggered (~1 dot per
# 300m), while a pause or slow stroll still gets a dot at least every 5 min,
# which caps how much time one dot's position stands in for.
# On testing/data/20260830.geojson: 238 dots, median spacing ~304m, 7
# expo_calx calls (the earlier 0.0009/90s gave ~560 dots, ~110m, 15 calls).
# Raise both values for a sparser map, lower them for a denser one.
TRAIL_DOT_DEGREE_THRESHOLD = 0.0027
TRAIL_DOT_MAX_SECONDS = 300.0

# A trail breaks (never simplified across) when one run lasts longer than
# MAX_GAP_SECONDS - a run's end is the next ping's time, so a long run in a
# moving trail means a GPS dropout - or when two consecutive runs' points are
# more than MAX_GAP_METERS apart (e.g. GPS drops in a tunnel and resumes far
# away). Both endpoints of a real gap therefore survive as their own dots.
#
# 200m / 120s mirror tools/process_geojson.js's own independently-chosen
# distGap=200/timeGap=120 for the same kind of "is this still one continuous
# GPS trail, or a real break" test on this same device/export data, and sit
# comfortably above the typical few-meters/few-seconds gap between
# consecutive pings during normal continuous tracking in the sample data
# (testing/data/*.geojson), while well below the multi-hundred-meter/
# multi-minute jumps a genuine dropout produces.
MAX_GAP_METERS = 200.0
MAX_GAP_SECONDS = 120.0
