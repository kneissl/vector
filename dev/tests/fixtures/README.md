# Captured memory snapshots

Real `/api/memory-snapshot` dumps from hardware, used as decode regression
fixtures. Format: 8192 space-separated decimal bytes on one line.

| file | machine | notes |
|---|---|---|
| `fishtales_L5_baseline.txt` | Fish Tales, Vector fw 1.7.16 | 2026-09-02. Every counter cross-checked against the operator menu on-site (12/12). |
| `fishtales_L5_two_coins.txt` | same machine | after one left-slot and one right-slot coin |

Fish Tales is the second WPC title mapped, and the one that proved the counter
*format* is per-game (big-endian, check byte at +5) rather than WPC-constant.
