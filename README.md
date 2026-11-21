# Builders Buildathon: Mech Royale bot (team 2.3%)

A Python bot for the Mech Royale track. Placement counts, not kills, so it stays inside the next
zone circle, moves quietly, backs away from enemies and faces whoever hits it.

- `bot.py`: our bot (set `VARIANT` at the top).
- `mechfight.py`: the stdin/stdout wrapper from the Builders mechfight kit (`python/mechfight.py`), unchanged.
- `Dockerfile`: python:3.13-slim, standard library only, no network needed.

Prior code used: only the Builders kit wrapper above. Released under the MIT License.

## Shopping Sessions track

Our plan for the Shopping Sessions track is in [`shopping-sessions/STRATEGY.md`](shopping-sessions/STRATEGY.md).
