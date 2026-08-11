from pathlib import Path


def vced_to_vmem(vced: bytes) -> bytes:
    if len(vced) != 155:
        raise ValueError(f"Expected 155 VCED bytes, got {len(vced)}")

    vmem = bytearray(128)
    for op in range(6):
        src = op * 21
        dst = op * 17
        vmem[dst : dst + 11] = vced[src : src + 11]
        vmem[dst + 11] = (vced[src + 11] & 3) | ((vced[src + 12] & 3) << 2)
        vmem[dst + 12] = (vced[src + 13] & 7) | ((vced[src + 20] & 15) << 3)
        vmem[dst + 13] = (vced[src + 14] & 3) | ((vced[src + 15] & 7) << 2)
        vmem[dst + 14] = vced[src + 16] & 0x7F
        vmem[dst + 15] = (vced[src + 17] & 1) | ((vced[src + 18] & 31) << 1)
        vmem[dst + 16] = vced[src + 19] & 0x7F

    vmem[102:110] = vced[126:134]
    vmem[110] = vced[134] & 31
    vmem[111] = (vced[135] & 7) | ((vced[136] & 1) << 3)
    vmem[112:116] = vced[137:141]
    vmem[116] = (vced[141] & 1) | ((vced[142] & 7) << 1) | ((vced[143] & 7) << 4)
    vmem[117] = vced[144] & 0x7F
    vmem[118:128] = vced[145:155]
    return bytes(vmem)


def checksum(data: bytes) -> int:
    return (-sum(data)) & 0x7F


single_path = Path(r"C:\Users\mrcll\AppData\Roaming\DigitalSuburban\Dexed\Cartridges\UFO.syx")
template_path = Path(r"C:\Users\mrcll\AppData\Roaming\DigitalSuburban\Dexed\Cartridges\Toby\Tobys1.syx")
output_path = single_path.with_name("UFO_Dexed_Bank.syx")

single = single_path.read_bytes()
template = template_path.read_bytes()
if len(single) != 163 or single[:2] != bytes((0xF0, 0x43)) or single[3:6] != bytes((0x00, 0x01, 0x1B)) or single[-1] != 0xF7:
    raise ValueError("Input is not a standard 163-byte DX7 single-voice dump")
if checksum(single[6:161]) != single[161]:
    raise ValueError("Single-voice checksum is invalid")
if len(template) != 4104 or template[:2] != bytes((0xF0, 0x43)) or template[3:6] != bytes((0x09, 0x20, 0x00)) or template[-1] != 0xF7:
    raise ValueError("Template is not a standard 4104-byte DX7 bank dump")

bank_data = bytearray(template[6:4102])
bank_data[:128] = vced_to_vmem(single[6:161])
output = bytes((0xF0, 0x43, 0x00, 0x09, 0x20, 0x00)) + bytes(bank_data)
output += bytes((checksum(bank_data), 0xF7))
output_path.write_bytes(output)

print(output_path)
print(f"size={len(output)} checksum=0x{output[-2]:02X} voice1={output[6+118:6+128].decode('ascii')!r}")
