import psutil
me = psutil.Process().pid
for p in psutil.process_iter(["pid", "name", "cmdline"]):
    try:
        if p.info["pid"] == me:
            continue
        cl = " ".join(p.info["cmdline"] or [])
        if "gateway" in cl and "notion-agent-gw" in cl.replace(chr(92), "/"):
            print("kill", p.info["pid"], cl[:90])
            p.kill()
    except Exception:
        pass
print("done")
