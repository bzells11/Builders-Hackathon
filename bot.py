"""Ghost bot for Mech Royale: placement beats kills, so stay inside the next circle,
stay quiet, face threats and fight only when found.  VARIANT: A zone only, B +evade/face, C +loot."""
import math
import mechfight as mf

VARIANT = "C"


def ang_diff(a, b):
    return (a - b + math.pi) % (2 * math.pi) - math.pi


class Bot:
    name = "ghost-" + VARIANT

    def __init__(self, hello):
        cfg = hello["config"]
        self.reach = cfg["loot"]["pickup_radius"]
        self.scan = 0.0
        self.threat_yaw = None
        self.threat_until = -1.0

    def act(self, obs):
        me = obs["self"]
        x, y, z = me["pos"]
        t = obs["t"]
        zone = obs["zone"]
        act = {"tick": obs["tick"]}
        if obs["offer"]:
            ids = [c["id"] for c in obs["offer"]["choices"]]
            act["pick"] = ids.index("armor") if "armor" in ids else 0

        # --- where to be: inside the NEXT circle, early
        nc, nr = zone["next_center"], zone["next_radius"]
        cc, cr = zone["center"], zone["radius"]
        d_next = math.dist((x, y), nc)
        d_cur = math.dist((x, y), cc)
        mv = [0.0, 0.0]
        boost = False
        if not me["in_zone"] or d_cur > cr * 0.9:
            tx, ty = cc
            boost = not me["in_zone"] and me["energy"] > 20
            speed = 1.0
        elif d_next > nr * 0.55:
            tx, ty = nc
            speed = 1.0 if (zone["shrink_start"] - t) < 25 or t >= zone["shrink_start"] else 0.4
        else:
            tx, ty, speed = x, y, 0.0
        if speed > 0:
            yaw_go = math.atan2(ty - y, tx - x)
            mv = [math.cos(yaw_go) * speed, math.sin(yaw_go) * speed]

        # --- threats
        enemies = [m for m in obs["mechs"] if m["kind"] == "mech"]
        enemies.sort(key=lambda m: math.dist(m["pos"][:2], (x, y)))
        for ev in obs["events"]:
            if ev["type"] == "damaged" and ev.get("bearing") is not None:
                self.threat_yaw = ev["bearing"]
                self.threat_until = t + 3.0

        aim_yaw = None
        aim_pitch = 0.0
        fire = False
        if enemies:
            e = enemies[0]
            ex, ey, ez = e["pos"]
            d = math.dist((ex, ey), (x, y))
            vx, vy = e.get("vel", [0, 0, 0])[:2]
            lead = d / 252.0
            px, py = ex + vx * lead, ey + vy * lead
            aim_yaw = math.atan2(py - y, px - x)
            aim_pitch = math.atan2((ez + 4.5) - (z + 7.0), max(d, 1.0))
            fire = d < 100 and me["weapons"][0] is not None
            if VARIANT in ("B", "C") and d < 90:
                # back away from the enemy while staying roughly toward the safe circle
                away = math.atan2(y - ey, x - ex)
                bx, by = math.cos(away), math.sin(away)
                gx, gy = (nc[0] - x, nc[1] - y)
                gn = math.hypot(gx, gy) or 1.0
                mv = [0.7 * bx + 0.3 * gx / gn, 0.7 * by + 0.3 * gy / gn]
        elif t < self.threat_until and self.threat_yaw is not None:
            aim_yaw = self.threat_yaw
        else:
            self.scan += 0.35
            aim_yaw = (math.atan2(ny := (nc[1] - y), nc[0] - x) + math.sin(self.scan) * 1.6)

        # --- loot (variant C)
        if VARIANT == "C":
            near = [p for p in obs["pickups"] if math.dist(p["pos"][:2], (x, y)) < self.reach and p["kind"] != "gun"]
            have = {w["kind"] for w in me["weapons"] if w}
            near = [p for p in near if p["kind"] not in have]
            if near and (me["weapons"][1] is None):
                act["take"] = near[0]["id"]
            elif not enemies and obs["pickups"] and d_next < nr * 0.9:
                p = min(obs["pickups"], key=lambda q: math.dist(q["pos"][:2], (x, y)))
                if math.dist(p["pos"][:2], (x, y)) < 35 and p["kind"] != "gun" and p["kind"] not in have:
                    ya = math.atan2(p["pos"][1] - y, p["pos"][0] - x)
                    mv = [math.cos(ya) * 0.6, math.sin(ya) * 0.6]

        # reload when quiet
        gun = me["weapons"][0]
        if not enemies and gun and gun.get("ammo", 12) < 6 and gun.get("reload", 0) == 0:
            act["reload"] = True

        act.update(move=mv, boost=boost, aim=[aim_yaw, aim_pitch], fire=[fire, fire and me["weapons"][1] is not None])
        mf.draw.label(f"{VARIANT} hp {me['hp']:.0f} alive {obs['alive']}")
        return act


mf.run(Bot)
