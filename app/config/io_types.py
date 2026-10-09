"""
Settings for each io type (Home, Other Indoor, Outdoor, and later Office,
Bus, ...):
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

# --- Other Indoor ---
# Any indoor stay that isn't Home.
OTHER_INDOOR_NAME = "Other Indoor"
OTHER_INDOOR_API_LABEL = "Other Indoor"

# --- Outdoor ---
# Fallback: any stay no other io type claims.
OUTDOOR_NAME = "Outdoor"
OUTDOOR_API_LABEL = "Outdoor"
