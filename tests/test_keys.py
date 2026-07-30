from app.core.keys import generate_key, hash_key, key_prefix


def test_generated_key_has_prefix_and_length():
    key = generate_key()
    assert key.startswith("lk_")
    assert len(key) == 35


def test_generated_keys_are_unique():
    assert generate_key() != generate_key()


def test_hash_is_sha256_hex_of_key():
    assert hash_key("lk_abc") == (
        "45f7da3757385412af05fa3c8f1fcbe3209d09d922f858f58b1656e88bea7fff"
    )


def test_prefix_is_first_eight_chars():
    key = generate_key()
    assert key_prefix(key) == key[:8]
