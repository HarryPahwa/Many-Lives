// Web client (TDD §18).
// Chat loop generates a client turn_id via crypto.randomUUID() and resends the
// same id on retry; input disabled while a turn is in flight. Minimap from
// /map, character panel from /player, context inspector from /debug/context.
// Scaffold only.
