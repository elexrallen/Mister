import json
from pathlib import Path

d = json.loads(Path("public/data/leagues/laliga-patio/latest_data.json").read_text(encoding="utf-8"))
pb = d.get("daily_playbook") or {}
print("fase:", pb.get("phase"), "|", pb.get("phase_label"), "|", pb.get("countdown_label"))
print("foco:", pb.get("focus"))
print("next_kickoff:", pb.get("next_kickoff"), "counts:", pb.get("counts"))
print("warnings:", pb.get("warnings"))
for c in pb.get("checklist") or []:
    print(f"  [{c['priority']}/{c['status']}] {c['title']} — {c['detail'][:110]}")
print("\nrecommendations:", len(d.get("recommendations") or []))
print("squad_notes:", len(d.get("squad_notes") or []))
for n in (d.get("squad_notes") or [])[:3]:
    print("  ", n.get("priority"), n.get("title"))
