"""Room service (TDD §7.3).

generate_room(): atomic claim -> plan (code) -> dress (model, injected) ->
validate -> retry/fallback -> commit RoomSpec + entities + CELL_GENERATED.
Never regenerates a generated room. Owned by Developer A (dresser from B).
Scaffold only.
"""
