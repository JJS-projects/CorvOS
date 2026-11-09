#!/usr/bin/env python3
"""Writes the hosted cases for `separate actor` (decisions 172 and 173): for each program below NAME.in and NAME.out
(what Fledgling prints for it) in the tests/hosted directory of every stage that can see the difference. Run it
again only to add a case, and read the new output."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bootstrap"))
from fledgling.dump import dump_tokens, dump_tree, dump_declarations, dump_bodies, dump_asm, make_stream

ACTOR = "separate actor Driver {\n    mut count: u64\n\n    new() {\n        .count = 0\n    }\n\n    lossy on add(n: u64) {\n        .count += n\n    }\n}\n"
SPAWNED = ACTOR + "\nfn start() {\n    d: Driver = spawn Driver()\n    d.add(5)\n}\n"
POINT = "struct Pt {\n    x: u64\n    y: u64\n}\n"
cases = {
    # (stage, name): (source, is a stream stage)
    ("lexdump", "separate_keyword"): "separate actor A {\n}\n",
    ("parsedump", "separate_actor"): ACTOR,
    ("parsedump", "pub_separate_actor"): "pub " + ACTOR,
    ("parsedump", "separate_before_fn"): "separate fn f() {\n}\n",
    ("parsedump", "separate_alone"): "separate\n",
    ("declsdump", "separate_actor_declared"): ACTOR,
    ("bodiesdump", "separate_actor_bodies"): SPAWNED,
    ("bodiesdump", "separate_handler_struct"): POINT + "separate actor Dev {\n    lossy on put(p: iso Pt) {\n    }\n}\n",
    ("bodiesdump", "separate_new_struct"): POINT + "separate actor Dev {\n    mut p: u64\n\n    new(q: val Pt) {\n        .p = 0\n    }\n}\n",
    ("asmdump", "separate_refused"): SPAWNED,
    ("asmdump", "separate_second_of_two"): "actor Plain {\n    lossy on ping() {\n    }\n}\n\n" + ACTOR,
}
REFERENCE = {"lexdump": dump_tokens, "parsedump": dump_tree, "declsdump": dump_declarations,
             "bodiesdump": lambda s: dump_bodies(s, ownership=True, actors=True, bodies="all"), "asmdump": dump_asm}
STREAM = {"declsdump", "bodiesdump", "asmdump"}
for (stage, name), source in cases.items():
    text = make_stream([("a.rook", False, source)]) if stage in STREAM else source
    here = os.path.join(ROOT, "tests", "hosted", stage)
    with open(os.path.join(here, name + ".in"), "w") as f:
        f.write(text)
    with open(os.path.join(here, name + ".out"), "w") as f:
        f.write(REFERENCE[stage](text))
    print(f"--- {stage}/{name}")
    print(REFERENCE[stage](text).rstrip().splitlines()[0][:150])
