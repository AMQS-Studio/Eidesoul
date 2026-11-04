"""WhatsApp address identity: a JID compares by its digits, LID and PN alike."""



def normalize_jid(value: str) -> str:
    """Digits of a JID; a value without digits is used as-is (lowercase)."""
    head = str(value or "").split("@")[0].split(":")[0]
    digits = "".join(ch for ch in head if ch.isdigit())
    return digits or str(value or "").lower()
