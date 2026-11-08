#!/usr/bin/env python3
"""Writes the cases of tests/hosted/asmdump: for each program below, NAME.in (the stream) and NAME.out (the
assembly Fledgling generates, `--dump-asm`). The programs are small and stand alone (no Raven), so the
expected text can be read: each exercises one kind of code the generator writes. Run this again only to add a
case, and read the new output."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bootstrap"))
from fledgling.dump import dump_asm, make_stream
here = os.path.join(ROOT, "tests", "hosted", "asmdump")
PT = "struct Pt {\n    x: u64\n    y: u64\n}\n"
cases = {
 "empty_fn": "fn f() {\n}\n",
 "return_value": "fn f() -> u64 {\n    return 7\n}\n",
 "big_literal": "fn f() -> u64 {\n    return 4294967296\n}\nfn g() -> i64 {\n    return -5\n}\n",
 "params": "fn add(a: u64, b: u8, c: bool) -> u64 {\n    return a\n}\n",
 "arith_u64": "fn f(a: u64, b: u64) -> u64 {\n    return a + b - a * b\n}\n",
 "arith_i8": "fn f(a: i8, b: i8) -> i8 {\n    return a + b - a * b + a / b\n}\n",
 "arith_i64": "fn f(a: i64, b: i64) -> i64 {\n    return a + b - a * b + a / b - a % b\n}\n",
 "arith_u16": "fn f(a: u16, b: u16) -> u16 {\n    return a + b - a * b + a / b + a % b\n}\n",
 "negate": "fn f(a: i32, b: i64) -> i64 {\n    return b - (-b) + (-a) as i64\n}\n",
 "bitwise": "fn f(a: u32, b: u32) -> u32 {\n    return not ((a and b) or (a xor b))\n}\n",
 "logic": "fn f(a: bool, b: bool) -> bool {\n    return not (a and b) or (a xor b)\n}\n",
 "shifts": "fn f(a: u8, b: u8, c: i32) -> i32 {\n    d: u8 = a shl b\n    e: u8 = a shr b\n    return (c shr 3) + (c shl 1)\n}\n",
 "compare": "fn f(a: i32, b: i32, c: u32, d: u32) -> bool {\n    return a < b or a >= b or c <= d or c > d or a == b or c != d\n}\n",
 "casts": "fn f(a: u8, b: i16) -> u64 {\n    return a as u64 + b wrap u64 + true as u64\n}\n",
 "locals": "fn f(a: u64) -> u64 {\n    mut b: u64 = a\n    b += 1\n    c: u64 = b\n    return c\n}\n",
 "if_else": "fn f(a: u64) -> u64 {\n    if a > 3 {\n        return 1\n    } else if a > 2 {\n        return 2\n    } else {\n        return 3\n    }\n}\n",
 "if_expr": "fn f(a: u64) -> u64 {\n    return if a > 3 { 1 } else { 2 }\n}\n",
 "loops": "fn f(n: u64) -> u64 {\n    mut s: u64 = 0\n    outer: loop s < n {\n        s += 1\n        if s == 5 {\n            break outer\n        }\n        if s == 3 {\n            continue outer\n        }\n    }\n    return s\n}\n",
 "for_range": "fn f(n: u64) -> u64 {\n    mut s: u64 = 0\n    each: loop i: u64 in 0..n {\n        s += i\n    }\n    each2: loop j: i8 in -3..3 {\n        s += 1\n    }\n    return s\n}\n",
 "match_numbers": "fn f(a: u8) -> u8 {\n    match a {\n        0 => return 1\n        1..5 => return 2\n        else => return 3\n    }\n}\n",
 "match_bool": "fn f(a: bool) -> u8 {\n    return match a {\n        true => 1\n        false => 2\n    }\n}\n",
 "struct_value": PT + "fn f(a: u64) -> u64 {\n    p: Pt = Pt(x: a, y: 2)\n    return p.x + p.y\n}\n",
 "struct_copy_assign": PT + "fn f(a: Pt) -> u64 {\n    mut p: Pt = copy(a)\n    p.x = 4\n    p = Pt(x: 1, y: 2)\n    return p.y\n}\n",
 "struct_result": PT + "fn f(a: u64) -> Pt {\n    return Pt(x: a, y: a)\n}\nfn g() -> u64 {\n    return f(3).y\n}\n",
 "struct_methods": "struct Counter {\n    n: u64\n\n    fn get() -> u64 {\n        return .n\n    }\n\n    mut fn bump(by: u64) {\n        .n += by\n    }\n}\nfn f(c: mut Counter) -> u64 {\n    c.bump(2)\n    return c.get()\n}\n",
 "struct_new": "struct Box {\n    v: u64\n\n    new(start: u64) {\n        .v = start\n    }\n}\nfn f() -> u64 {\n    b: Box = Box(start: 4)\n    return b.v\n}\n",
 "enum_match": "enum Shape {\n    Dot\n    Line(len: u64)\n    Rect(w: u64, h: u64)\n}\nfn area(s: Shape) -> u64 {\n    match s {\n        Dot => return 0\n        Line(len: l) => return l\n        Rect(w: w, h: h) => return w * h\n    }\n}\nfn f() -> u64 {\n    return area(Rect(w: 2, h: 3)) + area(Dot)\n}\n",
 "mut_param": "fn bump(n: mut u64) {\n    n += 1\n}\nfn f() -> u64 {\n    mut a: u64 = 1\n    bump(mut a)\n    return a\n}\n",
 "constants": "LIMIT: u64 = 10\nNEG: i32 = -3\nFLAG: bool = true\nfn f() -> u64 {\n    return LIMIT\n}\nfn g() -> i32 {\n    return NEG\n}\nfn h() -> bool {\n    return FLAG\n}\n",
 "big_frame": "fn f() -> u64 {\n" + "".join(f"    a{i}: u64 = {i}\n" for i in range(40)) + "    return a39\n}\n",
 "actor_send": "enum Sent {\n    Delivered\n    MailboxFull\n    Gone\n}\nstruct Pair {\n    a: u64\n    b: u64\n}\ninterface Add {\n    reliable on add(n: u64)\n}\nactor Counter: Add {\n    mut count: u64\n\n    new(start: u64) {\n        .count = start\n    }\n\n    reliable on add(n: u64) {\n        .count += n\n    }\n\n    pub lossy on pair(p: iso Pair) {\n        .count += p.a\n    }\n}\nfn f() {\n    c: Counter = spawn Counter(10)\n    a: Add = narrow(c, Add)\n    discard a.add(1)\n    c.pair(Pair(a: 1, b: 2))\n}\n",
 "panic_and_text": "enum Sent {\n    Delivered\n}\nstruct Bytes {\n    address: u64\n    length: u64\n}\nHELLO: Bytes = \"hi\"\nfn f() {\n    panic(\"stop\")\n}\nfn g() -> Bytes {\n    return HELLO\n}\n",
 "two_files": [("lib.rook", False, "pub fn helper() -> u64 {\n    return 1\n}\n"), ("a.rook", False, "import lib: helper\nfn f() -> u64 {\n    return helper()\n}\n")],
 "huge_frame": "struct Pt {\n    x: u64\n    y: u64\n}\nfn f() -> u64 {\n" + "".join(f"    a{i}: u64 = {i}\n" for i in range(520)) + "    p: Pt = Pt(x: 1, y: a519)\n    return p.y\n}\n",
 "break_in_match_argument": "fn g(a: u64) -> u64 {\n    return a\n}\nfn f(n: u64) -> u64 {\n    mut s: u64 = 0\n    each: loop {\n        s = 1 + match n {\n            0 => break each\n            else => g(1)\n        }\n    }\n    return s\n}\n",
 "error_unknown_name": "fn f() -> u64 {\n    return missing\n}\n",
}
for name, src in cases.items():
    files = src if isinstance(src, list) else [("a.rook", False, src)]
    stream = make_stream(files)
    open(f"{here}/{name}.in", "w").write(stream)
    open(f"{here}/{name}.out", "w").write(dump_asm(stream))
print(len(cases))
