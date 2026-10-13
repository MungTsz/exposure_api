"""
Settings for each io type (Home, Office, School, Other Indoor, Outdoor, and
later Bus, ...) and for matching place labels:
  - NAME: the label stored in the DB `io` column and returned to the app.
  - API_LABEL: the value sent to expo_calx in each request row's
    {"IO": ...}. expo_calx has its own vocabulary, so this can differ from
    NAME.
  - Any thresholds the io type's rule (app/io_types/rules/) uses.

One section per io type. To add a new one, see app/io_types/registry.py.
"""

# --- Indoor (shared by Home and Other Indoor) ---
# A stay lasting at least this long is indoor: Home or Other Indoor.
# NOTE on the "waiting for the bus" problem: a genuinely-indoor ~20min stop
# and an outdoor ~20min transit wait are, by design, indistinguishable by
# stay duration/box/speed alone - raising STAY_INDOOR_MINUTES (and/or
# tightening pipeline.STAY_DEGREE_THRESHOLD/SPEED_THRESHOLD_MPS) is a tuning
# tradeoff to reduce false-indoor bus-wait classifications, not a structural
# fix; it will also delay genuinely-indoor classification by the same
# amount.
STAY_INDOOR_MINUTES = 20

# --- Home ---
HOME_NAME = "Home"
HOME_API_LABEL = "Home"
# An indoor stay is Home if it starts at an HK-local hour in
# [HOME_START_HOUR_HK, HOME_END_HOUR_HK), i.e. 00:00-05:59 by default.
HOME_START_HOUR_HK = 0
HOME_END_HOUR_HK = 6

# --- Office ---
# Only assigned through a place label (see "Place labels" below).
OFFICE_NAME = "Office"
OFFICE_API_LABEL = "Office"

# --- School ---
# Only assigned through a place label (see "Place labels" below).
SCHOOL_NAME = "School"
SCHOOL_API_LABEL = "School"

# --- Other Indoor ---
# Any indoor stay that isn't Home.
OTHER_INDOOR_NAME = "Other Indoor"
OTHER_INDOOR_API_LABEL = "Other Indoor"

# --- Outdoor ---
# Fallback: any stay no other io type claims.
OUTDOOR_NAME = "Outdoor"
OUTDOOR_API_LABEL = "Outdoor"

# --- Place labels (app/io_types/place_labels.py) ---
# Circles users put on a map (PUT /place-labels/{label_id}). A stationary
# stay that a label matches gets the label's category as its io type,
# instead of the time-window rule above. Moving stays never use labels.
#
# Categories a label can have; each one is an io type NAME.
PLACE_LABEL_CATEGORIES = (HOME_NAME, OFFICE_NAME, SCHOOL_NAME, OTHER_INDOOR_NAME)
# A label matches a stay when its circle holds at least this fraction of
# the stay's pings. Checking all pings (not just the first) absorbs indoor
# GPS drift of 20-50 m.
PLACE_LABEL_MIN_PING_FRACTION = 0.5
# Two matching circles describe the same spot when their overlap area /
# union area is at least this. Labels for the same spot are put to a vote:
# the category with the most distinct contributors wins. A small circle
# inside a much bigger one is not the same spot, so the smaller (more
# specific) circle is not outvoted by the bigger one.
PLACE_LABEL_SAME_SPOT_MIN_OVERLAP = 0.5
