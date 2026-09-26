"""Manual OpenRouter smoke check; never run from the automated test suite."""

from app.config import get_settings
from app.domain.types import Role, RoomDressing
from app.harness.model_client import OpenRouterModelClient, role_defaults


def main() -> None:
    settings = get_settings()
    client = OpenRouterModelClient(settings)
    temperature, max_output_tokens, timeout_s = role_defaults(Role.DRESSER)
    result = client.structured(
        Role.DRESSER,
        (
            "Return only JSON matching the requested room dressing schema. "
            "Every feature must contain slot_id, kind, name, properties, and initial_state. "
            "Every entity must contain slot_id, name, description, persona, and traits. "
            "Every item must contain slot_id, name, and description."
        ),
        (
            "Create a grounded dark-fantasy dressing for a quiet stone room. "
            "Return exactly two features, one NPC entity, and one item. "
            "Use null for a feature slot_id only when no slot applies."
        ),
        RoomDressing,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        timeout_s=timeout_s,
    )
    vectors = client.embed(["A quiet dungeon room."])
    print(f"dresser_model={result.model}")
    print(f"embedding_model={settings.embedding_model}")
    print(f"embedding_dims={len(vectors[0])}")


if __name__ == "__main__":
    main()
