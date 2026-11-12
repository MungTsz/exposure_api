"""
Draws two flowcharts of how the pipeline decides the io type of every
moment (steps 0-4 of app/pipeline/run.py):

  docs/io_flowchart_simple.png  the decisions in plain words, for a first read
  docs/io_flowchart.png         every rule and threshold, enough to re-implement

Every threshold is read from app/config, so re-run this after changing one:

    python -m docs.io_flowchart

Needs the Graphviz `dot` program (`conda install graphviz`).
"""

import shutil
import subprocess
import sys
from html import escape
from pathlib import Path
from typing import Optional, Sequence, Union

from app.config import io_types as io
from app.config import pipeline as cfg
from app.config.constants import Reason

OUT_FULL = Path(__file__).with_suffix(".png")
OUT_SIMPLE = Path(__file__).with_name("io_flowchart_simple.png")
DPI = 130
FONT = "Arial"

# 1 m ~ 0.00001 deg in Hong Kong (app/config/pipeline.py)
M_PER_DEG = 100_000

IO_FILL = {
    io.HOME: "#FAD7A0",
    io.OFFICE: "#AED6F1",
    io.SCHOOL: "#A3E4D7",
    io.OTHER_INDOOR: "#D7DBDD",
    io.OUTDOOR: "#ABEBC6",
    io.MTR: "#F5B7B1",
    io.VEHICLE: "#F9E79F",
}
DECISION = 'shape=diamond style=filled fillcolor="#FFFFFF" color="#B7950B" penwidth=1.4'
PROCESS = 'shape=box style="rounded,filled" fillcolor="#F4F6F7" color="#566573"'
MOTION = 'shape=box style="rounded,filled,bold" fillcolor="#EAF2F8" color="#2E86C1"'
NOTE = 'shape=note style=filled fillcolor="#FEF9E7" color="#B7950B"'
JOIN = 'shape=point width=0.12 color="#566573"'


# --- value formatting ---

def deg(x: float) -> str:
    return f"{x:g} deg (~{x * M_PER_DEG:,.0f} m)"


def speed(x: float) -> str:
    return f"~{round(x * M_PER_DEG, 2):g} m/s"


def dur(s: float) -> str:
    return f"{s / 60:g} min" if s >= 600 else f"{s:g} s"


def hk_hours(hours) -> str:
    start, end = hours
    return f"{start:02d}:00-{end:02d}:00 HK"


# --- label building (Graphviz HTML-like labels) ---

def html(*lines: str, title: Optional[str] = None, small: Union[str, Sequence[str], None] = None) -> str:
    """`lines` are plain text (escaped here); `small` is a grey footnote of
    one or more lines."""
    parts = []
    if title:
        parts.append(f"<B>{escape(title)}</B>")
    parts += [escape(line) for line in lines]
    for line in [small] if isinstance(small, str) else small or ():
        parts.append(f'<FONT POINT-SIZE="10" COLOR="#626567">{escape(line)}</FONT>')
    return "<" + "<BR/>".join(parts) + ">"


def left(*lines: str, title: Optional[str] = None) -> str:
    """A left-aligned block of text lines."""
    head = f"<B>{escape(title)}</B><BR ALIGN=\"LEFT\"/>" if title else ""
    return "<" + head + "".join(f"{escape(line)}<BR ALIGN=\"LEFT\"/>" for line in lines) + ">"


class Dot:
    def __init__(self, title: str, subtitle: str, font_size: int = 12):
        self.lines = ["digraph io {"]
        self.add(f'graph [rankdir=TB fontname="{FONT}" fontsize=13 nodesep=0.3 ranksep=0.32 newrank=true '
                 f'compound=true bgcolor="white" pad=0.4 labelloc=t];')
        self.add(f'node [fontname="{FONT}" fontsize={font_size} margin="0.14,0.06"];')
        self.add(f'edge [fontname="{FONT}" fontsize={font_size - 1} color="#566573" arrowsize=0.8];')
        self.add(f'label=<<FONT POINT-SIZE="24"><B>{escape(title)}</B></FONT>'
                 f'<BR/><FONT POINT-SIZE="13" COLOR="#626567">{escape(subtitle)}</FONT><BR/> >;')

    def add(self, text: str) -> None:
        self.lines.append(text)

    def node(self, name: str, label: str, style: str = PROCESS) -> None:
        self.add(f"{name} [label={label} {style}];")

    def io_node(self, name: str, io_name: str, reason: Optional[str] = None, note: Optional[str] = None) -> None:
        small = ([f"reason={reason}"] if reason else []) + ([note] if note else [])
        self.node(name, html(title=io_name, small=small),
                  f'shape=box style="filled,bold" fillcolor="{IO_FILL[io_name]}" color="#424949"')

    def motion(self, name: str, motion: str, small: Optional[str] = None) -> None:
        self.node(name, html(title=motion, small=small), MOTION)

    def edge(self, a: str, b: str, label: str = "", extra: str = "") -> None:
        attrs = f'label=" {label} " ' if label else ""
        self.add(f"{a} -> {b} [{attrs}{extra}];")

    def yes(self, a: str, b: str) -> None:
        self.edge(a, b, "yes", 'fontcolor="#1E8449" color="#1E8449"')

    def no(self, a: str, b: str) -> None:
        self.edge(a, b, "no", 'fontcolor="#922B21" color="#922B21"')

    def same_rank(self, *names: str) -> None:
        self.add("{rank=same; " + "; ".join(names) + ";}")

    def cluster(self, name: str, title: str, source: str) -> None:
        self.add(f"subgraph cluster_{name} {{")
        self.add(f'label={html(title=title, small=source)}; labeljust=l; style="rounded,filled"; '
                 f'fillcolor="#FBFCFC"; color="#909497"; penwidth=1.2; margin=14;')

    def end(self) -> None:
        self.add("}")


def build_simple() -> str:
    """The same decisions in plain words: no step numbers, no motion names."""
    d = Dot(
        "How the io type of every moment is decided (simple version)",
        "Every rule and threshold: io_flowchart.png",
        font_size=14,
    )
    d.node("in", html("GPS points of the day", small="only time and position are used"))
    d.node("clean", html(
        "Clean up GPS noise",
        small=["drop points that jump far away and straight back;",
               f"judge speed over {cfg.V_NET_WINDOW_S / 60:g}-minute windows, so indoor jitter cancels out"],
    ))
    d.node("pieces", html(
        "Cut the day into pieces:",
        "each piece is moving or staying in one place",
        small=f"stops of up to {cfg.STOP_MAX_S / 60:g} min inside a trip (traffic light, bus stop) belong to the trip",
    ))
    d.node("kind", html("Moving or", "staying?"), DECISION)
    d.edge("in", "clean")
    d.edge("clean", "pieces")
    d.edge("pieces", "kind")

    d.node("mtr", html(
        "From one MTR station",
        "to another?",
        small=["GPS dark underground, or a track",
               f">= {cfg.RAIL_MIN_ROAD_M:g} m from the roads (trains run",
               "beside roads, cars on them)"],
    ), DECISION)
    d.node("fast", html("Faster than walking?", small=f"over {speed(cfg.WALK_MAX_V)}"), DECISION)
    d.io_node("s_mtr", io.MTR, note=f"also a stop of up to {dur(cfg.INTERCHANGE_MAX_S)} between two rides")
    d.io_node("s_vehicle", io.VEHICLE, note="taxi, bus, minibus, car ...")
    d.io_node("s_walk", io.OUTDOOR, note="walking")
    d.edge("kind", "mtr", "moving")
    d.yes("mtr", "s_mtr")
    d.no("mtr", "fast")
    d.yes("fast", "s_vehicle")
    d.no("fast", "s_walk")
    d.same_rank("mtr", "s_mtr")

    d.node("label", html(
        "Inside a place label?",
        small=["a circle someone put on the map", "as a Home / Office / School / Other Indoor"],
    ), DECISION)
    d.node("s_label", html(
        "The label's category",
        f"{io.HOME} / {io.OFFICE} / {io.SCHOOL} / {io.OTHER_INDOOR}",
        small="most labellers win when circles disagree",
    ), 'shape=box style="filled,bold" fillcolor="#FDEBD0" color="#424949"')
    d.node("night", html(
        f"At least {dur(io.INDOOR_MIN_S)}, and at least",
        f"partly in {hk_hours(io.HOME_HOURS)}?",
    ), DECISION)
    d.io_node("s_home_night", io.HOME, note="slept here")
    d.node("near", html(
        f"Within ~{io.HOME_RADIUS_DEG * M_PER_DEG:.0f} m of a place",
        "where the person slept?",
    ), DECISION)
    d.io_node("s_home_near", io.HOME, note="home in the daytime")
    d.node("short", html(f"Shorter than {dur(io.INDOOR_MIN_S)}?"), DECISION)
    d.io_node("s_short", io.OUTDOOR, note="waiting for a bus, a quick shop visit")
    d.io_node("s_other", io.OTHER_INDOOR, note="a long stay somewhere else")
    d.edge("kind", "label", "staying")
    d.yes("label", "s_label")
    d.no("label", "night")
    d.yes("night", "s_home_night")
    d.no("night", "near")
    d.yes("near", "s_home_near")
    d.no("near", "short")
    d.yes("short", "s_short")
    d.no("short", "s_other")
    d.same_rank("label", "s_label")
    d.same_rank("night", "s_home_night")
    d.same_rank("near", "s_home_near")
    d.same_rank("short", "s_short")

    d.node("note", html(
        f"{io.OFFICE} and {io.SCHOOL} only come from place labels:",
        f"a long daytime stay without one is {io.OTHER_INDOOR}.",
    ), NOTE)
    d.edge("s_other", "note", extra="style=invis")
    d.add("}")
    return "\n".join(d.lines)


def build_full() -> str:
    d = Dot(
        "How the io type (micro-environment) of every moment is decided",
        "GPS pings in, one io type per run out. Thresholds are the current values in app/config "
        "(pipeline.py, io_types.py); code in app/pipeline/, step order in run.py.",
    )

    # --- definitions ---
    d.node("defs", left(
        "ping      one GPS fix: time, lng, lat. Nothing else is read.",
        "deg       distance between two positions = |dlat| + |dlng| in degrees; in HK 1 m ~ 0.00001 deg.",
        "          Speeds are deg/s (x 100,000 ~ m/s). Metres (stations, roads, labels) use a flat projection.",
        "interval  interval i = ping i-1 -> ping i:  dt = seconds,  dd = deg moved.",
        f"v_net     of ping i: j, k = first and last ping within +/-{cfg.V_NET_WINDOW_S / 2:g} s of ping i;",
        f"          v_net = max(deg(j, k) - NOISE_DEG {cfg.NOISE_DEG:g}, 0) / max(t_k - t_j, {cfg.V_NET_MIN_SPAN_S:g} s);"
        "  unknown if t_k = t_j.",
        "          Indoor jitter (10-20 m per ping) cancels out over the window; real movement adds up.",
        "run       consecutive intervals with the same motion. Its pings: the ping before its first interval",
        "          .. its last ping. Duration = time of its last ping - time of its first ping.",
        "centre    of a run = (median lng, median lat) of its pings.",
        f"station   'at a station' = <= {cfg.MTR_STATION_MAX_M:g} m from an MTR heavy-rail station entrance "
        "(OpenStreetMap, data/mtr_stations/).",
        "road      distance in metres to the nearest road centreline (Lands Department, data/road_centrelines/).",
        "in HK     point-in-polygon test against constants.HK_BOUNDARY.",
        title="Terms and units",
    ), 'shape=box style="filled" fillcolor="#FDFEFE" color="#909497" fontname="Consolas" fontsize=11')

    key = "".join(
        f'<TD BGCOLOR="{fill}" BORDER="1" CELLPADDING="5"><B>{escape(name)}</B></TD>' for name, fill in IO_FILL.items()
    )
    d.node("key", (
        '<<TABLE BORDER="0" CELLSPACING="4"><TR><TD><B>io types</B></TD>' + key + '</TR>'
        '<TR><TD><B>other</B></TD>'
        '<TD BGCOLOR="#EAF2F8" BORDER="1" CELLPADDING="5" COLSPAN="2">motion of an interval / run</TD>'
        '<TD BORDER="1" CELLPADDING="5" COLSPAN="2">decision (yes / no)</TD>'
        '<TD BGCOLOR="#F4F6F7" BORDER="1" CELLPADDING="5" COLSPAN="2">processing step</TD>'
        '<TD BGCOLOR="#FEF9E7" BORDER="1" CELLPADDING="5" COLSPAN="1">note</TD></TR></TABLE>>'
    ), "shape=plaintext")
    d.edge("key", "defs", extra="style=invis")

    d.node("input", html(
        "Raw records of one upload (GeoJSON features or GPX <trkpt>)",
        "keep time + position, drop records without them, sort by time",
        title="Input",
    ))
    d.edge("defs", "input", extra="style=invis")

    # --- step 0 ---
    d.cluster("s0", "Step 0 - Remove GPS spikes", "app/pipeline/spikes.py")
    d.node("s0_loop", html(
        "For every 3 consecutive pings A, B, C",
        small=f"B plus the pings right after it at exactly the same position count as one B "
              f"(if they span <= {dur(cfg.SPIKE_MAX_S)})",
    ))
    d.node("s0_legs", html("Both legs A->B and B->C", f">= {deg(cfg.SPIKE_MIN_DEG)}?"), DECISION)
    d.node("s0_back", html("C back near A?", f"deg(A, C) <= {cfg.SPIKE_RETURN_RATIO:g} x shorter leg"), DECISION)
    d.node("s0_fast", html("A->B or B->C faster", f"than {speed(cfg.SPIKE_MAX_V)},", "while A->C is not?"), DECISION)
    d.node("s0_drop", html("Drop B", small="then re-check the triple before it"), 'shape=box style="filled" fillcolor="#E5E7E9"')
    d.node("s0_keep", html("Keep B"), 'shape=box style="filled" fillcolor="#FFFFFF"')
    d.edge("s0_loop", "s0_legs")
    d.no("s0_legs", "s0_keep")
    d.yes("s0_legs", "s0_back")
    d.yes("s0_back", "s0_drop")
    d.no("s0_back", "s0_fast")
    d.yes("s0_fast", "s0_drop")
    d.no("s0_fast", "s0_keep")
    d.end()
    d.edge("input", "s0_loop")

    d.node("track", html(
        "Spike-free pings -> intervals (dt, dd) and v_net of every ping",
        title="Track",
    ))
    d.edge("s0_keep", "track")
    d.edge("s0_drop", "track")

    # --- step 1 ---
    d.cluster("s1", "Step 1 - Motion of each interval (first match wins)", "app/pipeline/motion.py")
    d.node("s1_jump", html(f"dd >= {deg(cfg.JUMP_DEG)}?"), DECISION)
    d.node("s1_still", html(f"dt >= {dur(cfg.GAP_S)},", "or v_net unknown,", f"or v_net < {speed(cfg.STILL_MAX_V)}?"),
           DECISION)
    d.node("s1_walk", html(f"v_net < {speed(cfg.WALK_MAX_V)}?"), DECISION)
    d.motion("m1_jump", "JUMP", "moved while GPS was dark")
    d.motion("m1_still", "STILL")
    d.motion("m1_walk", "WALK")
    d.motion("m1_vehicle", "VEHICLE")
    d.node("s1_note", html(
        "v_net of an interval = mean of its two end pings' v_net",
        "(ignoring an unknown one).",
        "Interval 0 has no duration: it copies interval 1.",
    ), NOTE)
    d.yes("s1_jump", "m1_jump")
    d.no("s1_jump", "s1_still")
    d.yes("s1_still", "m1_still")
    d.no("s1_still", "s1_walk")
    d.yes("s1_walk", "m1_walk")
    d.no("s1_walk", "m1_vehicle")
    d.same_rank("s1_jump", "m1_jump", "s1_note")
    d.same_rank("s1_still", "m1_still")
    d.same_rank("s1_walk", "m1_walk")
    d.end()
    d.edge("track", "s1_jump")

    # --- step 2 ---
    d.cluster("s2", "Step 2 - Runs, cleaned up", "app/pipeline/runs.py")
    d.node("s2_group", html("Group consecutive intervals with the same motion into runs"))
    d.node("s2_round", html(
        "Clean-up round: look for the first rule (2a, 2b, 2c) that applies",
        small="after every change, a run merges with neighbours of the same motion",
    ))
    d.node("s2_a", html(
        "2a stop inside a trip:",
        f"a STILL / WALK run <= {dur(cfg.STOP_MAX_S)}",
        "with VEHICLE / JUMP runs on both sides?",
    ), DECISION)
    d.node("s2_a_do", html("The first such run becomes VEHICLE", "(JUMP if neither side is VEHICLE)",
                           small="traffic light, bus stop"))
    d.node("s2_b", html(
        "2b GPS blip:",
        f"a VEHICLE run < {dur(cfg.MIN_VEHICLE_RUN_S)}",
        f"or a WALK run < {dur(cfg.MIN_WALK_RUN_S)}?",
    ), DECISION)
    d.node("s2_b_do", html(
        "The shortest such run joins a neighbour:",
        "a VEHICLE blip next to a JUMP run -> JUMP,",
        "else the motion of its longer neighbour",
        small="the earlier neighbour on a tie",
    ))
    d.node("s2_c", html(
        "2c same place:",
        f"STILL + WALK <= {dur(cfg.SAME_PLACE_MAX_WALK_S)} + STILL,",
        f"the two STILL centres <= {deg(cfg.SAME_PLACE_DEG)} apart?",
    ), DECISION)
    d.node("s2_c_do", html("The first such WALK becomes STILL:", "one stay", small="walking around a mall"))
    d.node("s2_d", html(
        "2d (once, after the loop):",
        "a JUMP run next to a VEHICLE run becomes VEHICLE",
        small="GPS lost during a road trip",
    ))
    d.edge("s2_group", "s2_round")
    d.edge("s2_round", "s2_a")
    d.yes("s2_a", "s2_a_do")
    d.no("s2_a", "s2_b")
    d.yes("s2_b", "s2_b_do")
    d.no("s2_b", "s2_c")
    d.yes("s2_c", "s2_c_do")
    d.edge("s2_c", "s2_d", "no: nothing changed", 'fontcolor="#922B21" color="#922B21"')
    for done in ("s2_a_do", "s2_b_do", "s2_c_do"):
        d.edge(done, "s2_round", "repeat", 'style=dashed constraint=false')
    d.same_rank("s2_a", "s2_a_do")
    d.same_rank("s2_b", "s2_b_do")
    d.same_rank("s2_c", "s2_c_do")
    d.end()
    for m in ("m1_jump", "m1_still", "m1_walk", "m1_vehicle"):
        d.edge(m, "s2_group")

    # --- step 3 ---
    d.cluster("s3", "Step 3 - MTR rides (3a-3d in order, runs regrouped after each)", "app/pipeline/mtr.py")
    d.node("s3a", html("3a  for every JUMP run", title=None))
    d.node("s3a_many", html(
        f"More than {cfg.MTR_MAX_JUMPS} intervals",
        f"with dd >= {deg(cfg.JUMP_DEG)}?",
    ), DECISION)
    d.node("s3a_short", html(
        f"deg(first ping, last ping) < {deg(cfg.MTR_MIN_DEG)},",
        "or first / last ping not at a station?",
    ), DECISION)
    d.motion("m3a_vehicle", "VEHICLE", "a phone on the road that logs rarely; 3b decides")
    d.motion("m3a_walk", "WALK", "too short / not station to station")
    d.motion("m3a_jump", "JUMP", "underground ride")
    d.yes("s3a_many", "m3a_vehicle")
    d.no("s3a_many", "s3a_short")
    d.yes("s3a_short", "m3a_walk")
    d.no("s3a_short", "m3a_jump")
    d.edge("s3a", "s3a_many")
    d.same_rank("s3a_many", "m3a_vehicle")
    d.same_rank("s3a_short", "m3a_walk")

    d.node("s3b", html("3b  rail test, for every VEHICLE run"))
    d.node("s3b_hk", html("Run centre", "inside HK?"), DECISION)
    d.node("s3b_rail", html(
        f"Median road distance of its pings >= {cfg.RAIL_MIN_ROAD_M:g} m",
        "and first + last ping at a station?",
    ), DECISION)
    d.motion("m3b_rail", "RAIL", "above-ground MTR: trains run beside the roads")
    d.motion("m3b_vehicle", "VEHICLE", "cars and buses drive on the centreline")
    d.no("s3b_hk", "m3b_vehicle")
    d.yes("s3b_hk", "s3b_rail")
    d.yes("s3b_rail", "m3b_rail")
    d.no("s3b_rail", "m3b_vehicle")
    d.edge("s3b", "s3b_hk")
    for m in ("m3a_vehicle", "m3a_walk", "m3a_jump"):
        d.edge(m, "s3b")
    d.same_rank("s3b_rail", "m3b_vehicle")

    d.node("s3c", html(
        "3c  time extension, for every JUMP run:",
        f"needed = deg(first ping, last ping) / {speed(cfg.MTR_MAX_AVG_V)}.",
        "While the run's duration < needed and the interval just before it is STILL,",
        "that interval becomes JUMP (the ride began earlier; those pings were stale).",
    ))
    d.edge("m3b_rail", "s3c")
    d.edge("m3b_vehicle", "s3c")
    d.node("s3d", html(
        "3d  interchange:",
        f"a STILL / WALK run <= {dur(cfg.INTERCHANGE_MAX_S)}",
        "between two MTR runs (JUMP / RAIL)?",
    ), DECISION)
    d.motion("m3d", "INTERCHANGE", "changing lines inside a station")
    d.yes("s3d", "m3d")
    d.edge("s3c", "s3d")
    d.same_rank("s3d", "m3d")
    d.node("s3_join", html("Every run now has a motion:", "JUMP, RAIL, INTERCHANGE, VEHICLE, WALK or STILL"))
    d.no("s3d", "s3_join")
    d.edge("m3d", "s3_join")
    d.end()
    d.edge("s2_d", "s3a")

    # --- step 4 ---
    d.cluster("s4", "Step 4 - io type of each run (decision tree)", "app/pipeline/classify.py")
    d.node("s4_motion", html("Motion of the run?"), DECISION)
    d.io_node("io_mtr", io.MTR, " / ".join(r.value for r in (Reason.JUMP, Reason.RAIL, Reason.INTERCHANGE)))
    d.io_node("io_vehicle", io.VEHICLE, Reason.VEHICLE.value, "taxi, bus, minibus, car ...")
    d.io_node("io_walk", io.OUTDOOR, Reason.WALK.value)
    d.node("s4_stay", html("A stay (STILL run)"))
    d.edge("s4_motion", "io_mtr", "JUMP / RAIL / INTERCHANGE")
    d.edge("s4_motion", "io_vehicle", "VEHICLE")
    d.edge("s4_motion", "io_walk", "WALK")
    d.edge("s4_motion", "s4_stay", "STILL")
    d.node("s4_label", html("A place label", "matches the stay?", small="see Place label match"), DECISION)
    d.node("io_label", html(
        "the label's category",
        f"{io.HOME} / {io.OFFICE} / {io.SCHOOL} / {io.OTHER_INDOOR}",
        small=f"reason={Reason.PLACE_LABEL.value}, io_source=label",
    ), 'shape=box style="filled,bold" fillcolor="#FDEBD0" color="#424949"')
    d.node("s4_night", html(
        f"Stay >= {dur(io.INDOOR_MIN_S)} and it overlaps",
        f"{hk_hours(io.HOME_HOURS)} on any day?",
    ), DECISION)
    d.io_node("io_home_night", io.HOME, Reason.NIGHT_STAY.value)
    d.node("s4_near", html(
        "Stay centre within",
        f"{deg(io.HOME_RADIUS_DEG)} of the",
        "centre of a night stay?",
    ), DECISION)
    d.io_node("io_home_near", io.HOME, Reason.NEAR_NIGHT_STAY.value)
    d.node("s4_short", html(f"Stay < {dur(io.INDOOR_MIN_S)}?"), DECISION)
    d.io_node("io_short", io.OUTDOOR, Reason.SHORT_STOP.value, "waiting, a quick shop visit")
    d.io_node("io_other", io.OTHER_INDOOR, Reason.LONG_STAY.value)
    d.node("s4_note", html(
        f"Night stay = any stay of the same upload that is >= {dur(io.INDOOR_MIN_S)},",
        f"overlaps {hk_hours(io.HOME_HOURS)} and has no place label",
        f"(or a {io.HOME} one).",
        f"{io.OFFICE} and {io.SCHOOL} are never guessed: only labels give them.",
        "io_source=inferred everywhere except place labels.",
    ), NOTE)
    d.edge("s4_stay", "s4_label")
    d.yes("s4_label", "io_label")
    d.no("s4_label", "s4_night")
    d.yes("s4_night", "io_home_night")
    d.no("s4_night", "s4_near")
    d.yes("s4_near", "io_home_near")
    d.no("s4_near", "s4_short")
    d.yes("s4_short", "io_short")
    d.no("s4_short", "io_other")
    d.same_rank("s4_label", "io_label")
    d.same_rank("s4_night", "io_home_night")
    d.same_rank("s4_near", "io_home_near", "s4_note")
    d.same_rank("s4_short", "io_short")
    d.end()
    d.edge("s3_join", "s4_motion")

    # --- place label match ---
    d.cluster("pl", "Place label match for one stay", "app/pipeline/place_labels.py")
    d.node("pl_grow", html(
        "Grow every label circle's radius",
        f"by {io.PLACE_LABEL_DRIFT_MARGIN_M:g} m (indoor GPS drift)",
        small="labels are shared: every contributor's labels count",
    ))
    d.node("pl_cand", html(
        "Any circle holding >= ",
        f"{io.PLACE_LABEL_MIN_PING_FRACTION:.0%} of the stay's pings?",
    ), DECISION)
    d.node("pl_none", html("No label match"), 'shape=box style="filled" fillcolor="#FFFFFF"')
    d.node("pl_rank", html(
        "Rank those candidates: most pings inside,",
        "then smaller radius, then smaller label_id.",
        "best = the first.",
    ))
    d.node("pl_vote", html(
        "Voters = best + candidates whose overlap with best",
        f"(intersection area / union area) >= {io.PLACE_LABEL_SAME_SPOT_MIN_OVERLAP:g}.",
        "One vote per distinct contributor per category.",
    ))
    d.node("pl_win", html(
        "Category with the most votes",
        small="tie -> the category of the better-ranked circle",
    ), 'shape=box style="filled,bold" fillcolor="#FDEBD0" color="#424949"')
    d.edge("pl_grow", "pl_cand")
    d.no("pl_cand", "pl_none")
    d.yes("pl_cand", "pl_rank")
    d.edge("pl_rank", "pl_vote")
    d.edge("pl_vote", "pl_win")
    d.end()
    d.edge("s4_label", "pl_grow", extra='style=dotted arrowhead=none constraint=false color="#B7950B"')
    d.same_rank("s4_stay", "pl_grow")
    d.edge("s4_stay", "pl_grow", extra="style=invis")  # keeps the box right of the tree

    # --- after step 4 ---
    d.node("after", html(
        "Then (not part of the io decision): each run -> points (a stay = 1 point at its centre,",
        "a route = thinned line), each point's time split at HK clock hours, in-HK rows sent to expo_calx.",
        small="app/pipeline/points.py, time_split.py, request_rows.py",
    ), 'shape=box style="rounded,dashed" color="#909497" fontcolor="#424949"')
    d.edge("io_other", "after", extra='ltail=cluster_s4 style=dashed')

    d.add("}")
    return "\n".join(d.lines)


def find_dot() -> str:
    """`dot` on PATH, else the one a conda install puts next to Python."""
    found = shutil.which("dot")
    if found:
        return found
    for candidate in (Path(sys.prefix, "Library", "bin", "dot.exe"), Path(sys.prefix, "bin", "dot")):
        if candidate.exists():
            return str(candidate)
    sys.exit("Graphviz `dot` not found: conda install graphviz")


def main() -> None:
    dot = find_dot()
    for out, build in ((OUT_SIMPLE, build_simple), (OUT_FULL, build_full)):
        subprocess.run([dot, "-Tpng", f"-Gdpi={DPI}", "-o", str(out)], input=build().encode("utf-8"), check=True)
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
