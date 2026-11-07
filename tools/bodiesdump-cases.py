#!/usr/bin/env python3
"""Writes the cases of tests/hosted/bodiesdump: for each program below, NAME.in (the stream) and NAME.out
(what Fledgling prints with --dump-bodies --no-bodies). The values of the arithmetic, bitwise, shift, cast and
comparison cases were worked out by hand when the cases were made; run this again only to add a case, and read
the new output.
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bootstrap"))
from fledgling.dump import dump_bodies, make_stream
here = os.path.join(ROOT, "tests", "hosted", "bodiesdump")
BYTES = "pub struct Bytes {\n    pub address: u64\n    pub length: u64\n}\n"
cases = {
 "int_literals": "A: u64 = 18446744073709551615\nB: i64 = -9223372036854775808\nC: i8 = -128\nD: u8 = 255\nE: u8 = 'a'\nF: u32 = 7\n",
 "arithmetic": "A: u8 = 200 + 55\nB: u32 = 7 / 2\nC: i32 = -7 / 2\nD: i32 = -7 % 3\nE: u64 = 3 * 4 - 5\nF: i64 = -3 * -4\nG: u32 = 7 % 4\n",
 "bitwise": "A: u8 = 6 and 3\nB: u8 = 6 or 3\nC: u8 = 6 xor 3\nD: u8 = not 5\nE: i8 = not 5\nF: i8 = -6 and 3\nG: bool = true xor true\nH: bool = true and false\nI: bool = not true\n",
 "shifts": "A: u16 = 1 shl 15\nB: i32 = -8 shr 1\nC: u32 = 100 shr 3\nD: i32 = -1 shr 5\nE: u64 = 1 shl 63\nF: i8 = -64 shl 1\nG: i32 = -9 shr 1\n",
 "comparisons": "A: bool = 3 < 4\nB: bool = 3 >= 4\nM3: i32 = -3\nC: bool = M3 < 2\nD: bool = 5 == 5\nE: bool = 5 != 5\nF: bool = true == false\nG: bool = 7 <= 7\nH: bool = 7 > 7\n",
 "casts": "BYTE: u8 = 200\nA: u64 = BYTE as u64\nB: u8 = 300 wrap u8\nC: i8 = 200 wrap i8\nD: u16 = 'a' as u16\nE: u32 = true as u32\nNEGONE: i8 = -1\nF: u8 = NEGONE wrap u8\nG: i64 = 255 wrap i8 as i64\nH: u8 = 5 as u8\n",
 "if_expr": "A: u32 = if 3 < 4 { 10 } else { 20 }\nB: u8 = if false { 1 } else { 2 }\nC: i32 = if true { -1 } else { 1 }\n",
 "named": "BASE: u64 = 100\nNEXT: u64 = BASE + 1\nDOUBLE: u64 = NEXT * 2\nNEG: i32 = -5\nSUM: i32 = NEG * 3 + 1\nFLAG: bool = NEXT > BASE\n",
 "text": BYTES + "HELLO: Bytes = \"hi\"\nEMPTY: Bytes = \"\"\n",
 "error_text_from_name": BYTES + "HELLO: Bytes = \"hi\"\nOTHER: Bytes = HELLO\n",
 "no_constants": "fn f() {\n}\n",
 "empty_program": "",
 "imported_constant": [("lib.rook", False, "pub SHOWN: u64 = 5\n"), ("a.rook", False, "import lib: SHOWN\nA: u64 = SHOWN + 1\n")],
 "two_files": [("lib.rook", False, "pub A: u64 = 1\nB: u64 = 2\n"), ("a.rook", False, "pub C: u64 = 3\nfn f() {\n}\n")],
 "actor_and_interface": "interface Go {\n    lossy on go(n: u8)\n}\nactor Bot: Go {\n    mut hits: u64\n    new() {\n        .hits = 0\n    }\n    lossy on go(n: u8) {\n    }\n}\nLIMIT: u64 = 2\n",
 "struct_and_enum_methods": "struct Pt {\n    x: u64\n    fn get() -> u64 {\n        return .x\n    }\n}\nenum Mode {\n    Fast\n    Slow\n    fn label() -> u8 {\n        return 1\n    }\n}\nLIMIT: u64 = 2\n",
 "wide_bool": "A: bool = 1 < 2 and 3 < 4\n",
}
errors = {
 "error_literal_range": "A: u8 = 256\n",
 "error_literal_range_signed": "A: i8 = 128\n",
 "error_literal_negative_unsigned": "A: u8 = -1\n",
 "error_overflow": "A: u8 = 200 + 100\n",
 "error_overflow_mul": "A: u32 = 65536 * 65536\n",
 "error_overflow_sub": "A: u8 = 1 - 2\n",
 "error_division_zero": "A: u32 = 5 / 0\n",
 "error_modulo_zero": "A: u32 = 5 % 0\n",
 "error_shift_too_large": "A: u8 = 1 shl 9\n",
 "error_shift_loses_bits": "A: u8 = 255 shl 1\n",
 "error_shl_signed_loses": "A: i8 = 64 shl 1\n",
 "error_negate_unsigned": "A: u8 = 5\nB: u8 = -A\n",
 "error_not_text": BYTES + "A: Bytes = \"x\"\nB: bool = not A\n",
 "error_type_mismatch": "A: u8 = true\n",
 "error_mixed_binary": "A: u8 = 1\nB: u16 = 2\nC: u16 = A + B\n",
 "error_arith_on_bool": "A: bool = true + false\n",
 "error_logic_on_text": BYTES + "A: Bytes = \"x\"\nB: bool = A and A\n",
 "error_ordering_bool": "A: bool = true < false\n",
 "body_not_checked": "struct Pt {\n    x: u64\n}\nfn f(a: Pt, b: Pt) -> bool {\n    return a == b\n}\nA: bool = true\n",
 "error_if_branches": "A: u8 = if true { 1 } else { false }\n",
 "error_if_condition": "A: u8 = if 1 { 1 } else { 2 }\n",
 "error_cast_lossy": "A: u8 = 5\nB: u16 = 300\nC: u8 = B as u8\n",
 "error_cast_lossless_wrap": "A: u8 = 5\nB: u64 = A wrap u64\n",
 "error_cast_to_bool": "A: bool = 5 as bool\n",
 "error_cast_text": BYTES + "A: Bytes = \"x\"\nB: u64 = A as u64\n",
 "error_unknown_name": "A: u8 = MISSING\n",
 "error_function_as_name": "fn g() {\n}\nA: u8 = g\n",
 "error_type_as_value": "struct Pt {\n    x: u64\n}\nA: u8 = Pt\n",
 "error_unknown_type": "A: Missing = 1\n",
 "error_not_screaming": "limit: u64 = 1\n",
 "error_not_screaming_camel": "Limit: u64 = 1\n",
 "error_duplicate_constant": "A: u64 = 1\nA: u64 = 2\n",
 "error_call": "fn g() -> u64 {\n    return 1\n}\nA: u64 = g()\n",
 "error_call_unknown": "A: u64 = g()\n",
 "error_call_builtin": "A: u64 = load8(0)\n",
 "error_call_panic": "A: u64 = panic(\"x\")\n",
 "error_struct_literal": "struct Pt {\n    x: u64\n}\nA: Pt = Pt(x: 1)\n",
 "error_struct_literal_unknown": "A: u64 = Pt(x: 1)\n",
 "error_enum_literal": "enum Op {\n    Nop\n    Go(n: u64)\n}\nA: Op = Op(n: 1)\n",
 "error_variant_as_value": "enum Op {\n    Nop\n    Go(n: u64)\n}\nA: Op = Nop\n",
 "error_variant_with_fields": "enum Op {\n    Nop\n    Go(n: u64)\n}\nA: Op = Go\n",
 "error_consume": "A: u64 = consume(1)\n",
 "error_match": "A: u64 = match 1 {\n    else => 2\n}\n",
 "error_narrow": "A: u64 = narrow(1, X)\n",
 "error_spawn": "A: u64 = spawn Thing()\n",
 "error_self": "A: u64 = self\n",
 "error_self_value": "A: u64 = .\n",
 "error_copy_number": "A: u64 = copy(1)\n",
 "error_text_without_bytes": "A: u64 = \"x\"\n",
 "error_bytes_wrong_fields": "pub struct Bytes {\n    pub address: u64\n}\nA: u64 = \"x\"\n",
 "error_aggregate_not_text": BYTES + "A: Bytes = 5\n",
 "error_number_for_aggregate": BYTES + "B: Bytes = \"a\"\nA: Bytes = if true { B } else { B }\n",
 "error_not_constant": BYTES + "A: u64 = 5\nB: u64 = A\nC: u64 = if B == 5 { 1 } else { 2 }\nD: u64 = 1 + C\n",
 "error_message_order": "A: u8 = 1 + 300\n",
 "error_expected_in_unary": "A: i8 = -200\n",
 "error_arith_literal_second": "A: u8 = 1\nB: u8 = 300 + A\n",

 "error_overflow_u64_add": "A: u64 = 18446744073709551615 + 1\n",
 "error_overflow_u64_mul": "A: u64 = 4294967296 * 4294967296\n",
 "error_overflow_i64_negate": "A: i64 = -9223372036854775808\nB: i64 = -A\n",
 "error_literal_first_takes_type": "A: u8 = 5\nB: bool = 300 > A\n",
 "error_if_literal_first": "A: u8 = 5\nB: bool = if true { 300 } else { A }\n",
 "error_shift_by_width": "A: u8 = 1 shl 8\n",
 "error_shift_negative_amount": "A: i8 = 1\nB: i8 = 1 shl A\nC: i8 = -1\nD: i8 = 1 shl C\n",
 "error_private_constant": [("lib.rook", False, "HIDDEN: u64 = 5\n"), ("a.rook", False, "import lib: HIDDEN\nA: u64 = HIDDEN\n")],
 "error_private_constant_used": [("lib.rook", False, "HIDDEN: u64 = 5\n"), ("a.rook", False, "A: u64 = HIDDEN\n")],
 "error_constant_not_imported": [("lib.rook", False, "pub SHOWN: u64 = 5\n"), ("a.rook", False, "A: u64 = SHOWN\n")],
 "error_generic_variant_in_constant": "enum Maybe<T> {\n    Has(value: T)\n    Empty\n}\nA: Maybe<u64> = Empty\n",
 "error_type_in_other_file": [("lib.rook", False, "struct Hidden {\n    x: u64\n}\n"), ("a.rook", False, "A: Hidden = Hidden(x: 1)\n")],
 "error_private_fields_literal": [("lib.rook", False, "pub struct Pair {\n    pub a: u64\n    b: u64\n}\n"), ("a.rook", False, "import lib: Pair\nX: Pair = Pair(a: 1, b: 2)\n")],
 "error_generic_variant": "enum Maybe<T> {\n    Has(value: T)\n    Empty\n}\nA: u64 = Empty\n",
}
for name, src in {**cases, **errors}.items():
    files = src if isinstance(src, list) else [("a.rook", False, src)]
    stream = make_stream(files)
    open(f"{here}/{name}.in", "w").write(stream)
    open(f"{here}/{name}.out", "w").write(dump_bodies(stream, bodies=False))
print(len(cases), len(errors))
