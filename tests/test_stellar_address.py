from app.stellar_address import is_valid_account_id


ADDRESS = "GAAQCAIBAEAQCAIBAEAQCAIBAEAQCAIBAEAQCAIBAEAQCAIBAEAQDZ7H"
MUXED_ADDRESS = "MABAEAQCAIBAEAQCAIBAEAQCAIBAEAQCAIBAEAQCAIBAEAQCAIBAEAQCAIBAEAQCAIBAEAQCAKBZQ"


def test_valid_classic_and_muxed_addresses_are_accepted():
    assert is_valid_account_id(ADDRESS)
    assert is_valid_account_id(MUXED_ADDRESS)


def test_invalid_prefix_checksum_type_and_case_are_rejected():
    assert not is_valid_account_id("X" + ADDRESS[1:])
    assert not is_valid_account_id(ADDRESS[:-1] + "A")
    assert not is_valid_account_id(ADDRESS.lower())
    assert not is_valid_account_id(None)
    assert not is_valid_account_id(123)
