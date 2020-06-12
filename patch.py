#!/usr/bin/env python3

from shutil import copyfile
import binascii
import sys
import argparse
import hashlib

EXPECTED_SHA_HASH = '045488f037e346dca3404737dcc6951f146a2a37ba233dd307b5fead0429c151'

NEW_FUNCTION_BYTES = '5589e583ec3883ec046a016a206a01b8a0451508ffd083c4108945f483ec046a0168000200006a01b8a0451508ffd083c4108945f08b450883c00c8945ec8345ec0583ec046a106a00ff75ecb890280508ffd083c4108945e88345ec0983ec0cff75ecb850170508ffd083c4108945e48b45e483c00689c28b45f06689108b45f083c00266c70001658b45f083c00466c70001008b45f083c00666c70000008b45f083c0088b55e46689108b45e48d50018b45f083c00a83ec0452ff75ec50b860160508ffd083c410b89ce026088b008945e08b45e083c0548b008945dc837ddc007502eb5b8b45dc83c0088b008945d8837dd8007502eb488b45d88b008945d48b45d483c01c8945d08b45d08b008945cc8b45d0c7000b6c0c008b45f48b55f08910ff75e8ff75f4ff75d4ff75dcb8e0840608ffd083c4108b45d08b55cc8910c9c3'
PATCHED_JZ_CALL_BYTES = '9090909090909090909090908b450c8904249090909090e8bc720600'
NEW_COMMAND_NAME = '6368617400' # 'chat\0'
PATCHED_VERSION_CHECK = 'b804000000c3'

def hash_file(filename):
    h = hashlib.sha256()
    b = bytearray(128*1024)
    mv = memoryview(b)
    with open(filename, 'rb', buffering=0) as f:
        for n in iter(lambda : f.readinto(mv), 0):
            h.update(mv[:n])
    return h.hexdigest()

ELF_BASE = 0x08048000

AVAILBLE_BYTES = (0x080c0c6f - 0x080c0580)
assert len(NEW_FUNCTION_BYTES) < AVAILBLE_BYTES, 'overwriting! ' + str(len(NEW_FUNCTION_BYTES)) + ' >= ' + str(AVAILBLE_BYTES) 

ADD_RCON_WRITE_COMMAND = [
    (0x080592a8, PATCHED_JZ_CALL_BYTES),
    (0x080c0580, NEW_FUNCTION_BYTES),
    (0x081e5be3, NEW_COMMAND_NAME)
]

SKIP_GAME_VERSION_CHECK = [
    (0x080662e0, PATCHED_VERSION_CHECK)
]

offsets = ADD_RCON_WRITE_COMMAND + SKIP_GAME_VERSION_CHECK

def patch_file(input):
    output = input + '.patched'
    copyfile(input, output)
    input_hash = hash_file(input)
    if input_hash != EXPECTED_SHA_HASH:
        print('input binary must be an exact match, but has SHA256: ' + input_hash + ' (expected: ' + EXPECTED_SHA_HASH + ')')
        sys.exit(1)
    with open(output, 'r+b') as f:
        for (offset, patch) in offsets:
            raw_patch = binascii.unhexlify(patch)
            pos = offset - ELF_BASE
            f.seek(pos)
            f.write(raw_patch)
            print('wrote ' + str(len(raw_patch)) + ' bytes at 0x%08x' % pos)
    print('patched binary written to: ' + output)
    print('patched binary hash (SHA256): ' + hash_file(output))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="path to wargame server input")
    args = parser.parse_args()
    patch_file(args.input)
