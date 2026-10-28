"""
Tunable thresholds of the pipeline (app/pipeline), one section per step in
step order. They shape the pipeline's output, so they are the values worth
sweeping while checking results against data/samples/ in
notebooks/pipeline_walkthrough.ipynb. Change values here, not the logic.

Thresholds of the decision tree itself (how long a stay must be to count as
indoor, the Home hours, place label matching) live in io_types.py.

Units:
  deg  = |dlat| + |dlng| in degrees, the distance between two pings
         (rule of thumb in Hong Kong: 1 m ~ 0.00001 deg; a diagonal move
         reads up to ~1.4x longer than a north-south one)
  m    = metres (distances to roads, route shape)
  s    = seconds
"""

# --- Step 0: spikes (app/pipeline/spikes.py) ---
# Ping B of A, B, C is a spike when A->B and B->C are both at least
# SPIKE_MIN_DEG (~100 m) and either
#   - C is back within SPIKE_RETURN_RATIO of the shorter leg from A: GPS
#     jumped away and straight back, or
#   - reaching or leaving B takes more than SPIKE_MAX_V (~50 m/s, faster
#     than any HK road or MTR train) while A -> C directly does not: a
#     stale or overshooting fix (sample days: 100-450 m in 1-7 s, then
#     back on the track).
# Consecutive pings at exactly the same position count as one B (watch
# data repeats a fallback fix for minutes) as long as they span at most
# SPIKE_MAX_S; a longer one is a stay, never a spike.
SPIKE_MIN_DEG = 0.001
SPIKE_RETURN_RATIO = 0.25
SPIKE_MAX_V = 0.0005        # deg/s
SPIKE_MAX_S = 300

# --- Step 1: motion of each interval (app/pipeline/motion.py) ---
# Indoor GPS jitter moves a still phone by ~0.0001-0.0002 deg (10-20 m) per
# ping whatever the time between pings, so a ping's own speed is useless.
# v_net of a ping = (net displacement between the first and last ping within
# V_NET_WINDOW_S around it, minus NOISE_DEG, at least 0) / their time span.
NOISE_DEG = 0.0003          # ~30 m: GPS jitter floor
V_NET_WINDOW_S = 120        # centred on the ping
# An interval is, in this order:
JUMP_DEG = 0.003            # jump  if it moved >= ~300 m (travelled while GPS was dark)
GAP_S = 120                 # still if it is this long (no pings in between) ...
STILL_MAX_V = 0.000005      # ... or its v_net is below ~0.5 m/s (deg/s)
WALK_MAX_V = 0.00003        # walk  if its v_net is below ~3 m/s (deg/s); else vehicle
# A phone that logs only every minute or so while moving (e.g. a watch on a
# bus) also moves >= JUMP_DEG between pings: see MTR_MAX_JUMPS in step 3.

# --- Step 2: runs (app/pipeline/runs.py) ---
# A still / walk run this short between two vehicle / jump runs is a stop
# inside the trip (traffic light, bus stop).
STOP_MAX_S = 180
# Shorter runs are GPS blips and join a neighbour.
MIN_VEHICLE_RUN_S = 120
MIN_WALK_RUN_S = 60
# Two still runs whose centres are this close, with a walk of at most
# SAME_PLACE_MAX_WALK_S between them, are one stay (walking around a mall).
SAME_PLACE_DEG = 0.0008     # ~80 m
SAME_PLACE_MAX_WALK_S = 300

# --- Step 3: MTR (app/pipeline/mtr.py) ---
# Sparse logging: an underground ride is one long GPS-dark jump, or a few if
# the phone gets a fix at some stations (data/samples/1009, Kwun Tong line:
# 4 jumps). A jump run with more jumps than this is a moving phone that just
# logs rarely (watch data: 7-20 jumps per ride along the roads), so it is
# treated as a vehicle run and the rail test below decides. When the two
# can't be told apart, the default is Vehicle.
MTR_MAX_JUMPS = 5
# A jump run whose first and last pings are closer than this (~500 m, under
# one MTR stop) is not an underground ride but a walk the GPS caught up
# with late or logged rarely: real rides on the sample days covered
# 0.012 deg and more, the walks taken for MTR 0.0033-0.0039 deg
# (data/samples/1010 21:45, 20260901 14:34). When in doubt, Outdoor.
MTR_MIN_DEG = 0.005
# Rail test: a vehicle run inside Hong Kong whose pings are at a median of
# at least this many metres from the nearest road centreline is
# above-ground MTR. A car / bus drives on the centreline (road trips so
# far: 2-6 m); a train runs beside the roads (East Rail line: 24 m).
RAIL_MIN_ROAD_M = 15
# Door-to-door MTR speed (~15 m/s, deg/s). A jump faster than this started
# earlier (the pings before it were stale), so the still time right before
# it joins the ride.
MTR_MAX_AVG_V = 0.00015
# A still / walk run this short between two MTR runs is changing lines
# inside a station.
INTERCHANGE_MAX_S = 10 * 60

# --- Step 5: output points (app/pipeline/points.py) ---
# A route (walk / vehicle / MTR run) is thinned by Douglas-Peucker: the
# thinned line stays within ROUTE_TOL_M of every ping, about a road's
# width, so every turn, ramp and junction is kept. On the sample days,
# "one point every 100 m" cut corners by up to 72 m; Douglas-Peucker kept
# fewer points and never strays more than this.
ROUTE_TOL_M = 15
# ... plus at least one point every ~300 m on long straight roads, so the
# client has direction arrows and each point stands for a short stretch.
ROUTE_MAX_GAP_DEG = 0.003
# A Vehicle point names its road when a centreline is this close.
ROAD_NAME_MAX_M = 30
