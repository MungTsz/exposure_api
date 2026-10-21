"""
The io types, and the thresholds of the decision tree that picks one for
each run (app/pipeline/classify.py).

Each io type has a name (stored in the DB `io` column and returned to the
app) and an API label (sent to expo_calx as {"IO": label}). expo_calx has
its own vocabulary, so the two can differ.
"""

# --- Names ---
HOME = "Home"
OFFICE = "Office"
SCHOOL = "School"
OTHER_INDOOR = "Other Indoor"
OUTDOOR = "Outdoor"
MTR = "MTR"
VEHICLE = "Vehicle"  # taxi, bus, minibus, shuttle bus, car, motorcycle

# --- expo_calx labels ---
# If expo_calx renames one, change it here, nothing else.
API_LABELS = {
    HOME: "Home",
    OFFICE: "Office",
    SCHOOL: "School",
    OTHER_INDOOR: "Other Indoor",
    OUTDOOR: "Outdoor",
    MTR: "MTR",
    VEHICLE: "Vehicle",
}
IO_TYPES = tuple(API_LABELS)

# --- Stays (still runs without a place label) ---
# A stay at least this long is indoor; a shorter one is Outdoor (waiting for
# a bus and a quick shop visit look the same).
INDOOR_MIN_S = 20 * 60
# Home: a long stay overlapping these HK hours [start, end) of any day ...
HOME_HOURS = (0, 6)
# ... and any other stay of the same upload within this distance of such a
# night stay (|dlat| + |dlng|, ~100 m).
HOME_RADIUS_DEG = 0.001
# Office and School are never guessed: without a place label they are
# Other Indoor.

# --- Place labels (app/pipeline/place_labels.py) ---
# Circles users put on a map (PUT /place-labels/{label_id}). A stay inside a
# label gets the label's category, whatever the rules above say (a night at
# a labelled office is Office).
PLACE_LABEL_CATEGORIES = (HOME, OFFICE, SCHOOL, OTHER_INDOOR)
# Indoor GPS drifts 20-50 m, so every circle is grown by this much ...
PLACE_LABEL_DRIFT_MARGIN_M = 30.0
# ... and matches a stay when it holds at least this share of its pings.
PLACE_LABEL_MIN_PING_FRACTION = 0.5
# Two matching circles describe the same spot when their overlap area /
# union area is at least this. Labels for the same spot are put to a vote:
# the category with the most distinct contributors wins. A small circle
# inside a much bigger one is not the same spot, so the smaller (more
# specific) circle is not outvoted by the bigger one.
PLACE_LABEL_SAME_SPOT_MIN_OVERLAP = 0.5
