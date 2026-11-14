"""Live checks for optional post-instruction watch sampling, no code decoding."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from driver.harness.facade import Harness


def main():
    checks = []
    with Harness() as h:
        status = h.client.status()
        assert status["watch_count"] == 0
        address = status["tick_probe_addr"]
        for size in (1, 2, 4, 8):
            baseline = h.client.watch_list()["head"]
            watch = h.client.watch_set(address, size=size, mode="w", sample=True, depth=1)
            try:
                h.client.wait_ticks(5)
                result = h.client.watch_drain(since=baseline, watch_id=watch["id"])
            finally:
                h.client.watch_clear(watch["id"])
            hits = result["hits"]
            for hit in hits:
                hit.pop("code_hex", None)
            values = [int.from_bytes(bytes.fromhex(x["value_hex"]), "little") for x in hits if x.get("value_ok")]
            flags = {
                "sample_echo": watch.get("sample") is True,
                "all_values": bool(hits) and all(x.get("value_ok") and x.get("value_size") == size and len(x.get("value_hex", "")) == size * 2 for x in hits),
                "counter_changes": len(set(values)) >= 2,
                "qpc_positive": result.get("qpc_frequency", 0) > 0 and all(x.get("qpc", 0) > 0 for x in hits),
                "qpc_monotonic": [x["qpc"] for x in hits] == sorted(x["qpc"] for x in hits),
                "tick_counter_phase": bool(hits) and all(v == (x["tick"]+1) % (1 << (8*size)) for x,v in zip(hits,values)),
            }
            checks.append({"size":size,"checks":flags,"hits":hits,"metadata":{k:v for k,v in result.items() if k!='hits'}})
        baseline = h.client.watch_list()["head"]
        watch = h.client.watch_set(address, size=8, depth=1)
        try:
            h.client.wait_ticks(4)
            result = h.client.watch_drain(since=baseline, watch_id=watch["id"])
        finally:
            h.client.watch_clear(watch["id"])
        checks.append({"sample_off":bool(result['hits']) and all("value_hex" not in x and "qpc" not in x for x in result['hits'])})
        checks.append({"no_watches_left":h.client.status()["watch_count"] == 0})
    Path("out/liquid-blip-dynamic-review/watch-value-canary.json").write_text(json.dumps(checks,indent=1),encoding="utf-8")
    flags = [value for c in checks for value in c.get("checks", {k:v for k,v in c.items() if isinstance(v,bool)}).values()]
    print(json.dumps({"passed":sum(flags),"total":len(flags),"checks":checks},ensure_ascii=False))
    return 0 if all(flags) else 1


if __name__ == "__main__":
    raise SystemExit(main())
