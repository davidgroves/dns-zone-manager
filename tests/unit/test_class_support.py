"""Unit tests for DNS record class support."""

from dns_zone_manager.dns.types import (
    CLASS_CODE_TO_NAME,
    COMMON_CLASSES,
    DNS_RECORD_CLASSES,
    PROTECTED_CLASSES,
    UPDATABLE_CLASSES,
    RRClass,
    get_class_code,
    get_class_name,
    get_supported_classes,
    is_valid_class,
    normalize_class,
)


class TestRRClass:
    """Tests for the RRClass enum."""

    def test_common_classes_have_correct_values(self):
        """Test that common record classes have the correct IANA values."""
        assert RRClass.IN == 1
        assert RRClass.CH == 3
        assert RRClass.HS == 4
        assert RRClass.NONE == 254
        assert RRClass.ANY == 255

    def test_reserved_class(self):
        """Test that reserved class is defined."""
        assert RRClass.RESERVED == 0


class TestDNSRecordClasses:
    """Tests for DNS_RECORD_CLASSES mapping."""

    def test_all_common_classes_defined(self):
        """Test that all common record classes are defined."""
        common_classes = ["IN", "CH", "HS", "NONE", "ANY"]
        for rclass in common_classes:
            assert rclass in DNS_RECORD_CLASSES, f"{rclass} should be in DNS_RECORD_CLASSES"

    def test_class_info_has_required_fields(self):
        """Test that RecordClassInfo has all required fields."""
        for name, info in DNS_RECORD_CLASSES.items():
            assert info.class_code >= 0, f"{name} should have valid class_code"
            assert info.name == name, f"{name} name should match key"
            assert info.description, f"{name} should have description"

    def test_in_class_info(self):
        """Test IN class info."""
        info = DNS_RECORD_CLASSES["IN"]
        assert info.class_code == 1
        assert info.name == "IN"
        assert "Internet" in info.description
        assert info.rfc == "RFC1035"

    def test_ch_class_info(self):
        """Test CH (Chaos) class info."""
        info = DNS_RECORD_CLASSES["CH"]
        assert info.class_code == 3
        assert info.name == "CH"
        assert "Chaos" in info.description

    def test_hs_class_info(self):
        """Test HS (Hesiod) class info."""
        info = DNS_RECORD_CLASSES["HS"]
        assert info.class_code == 4
        assert info.name == "HS"
        assert "Hesiod" in info.description


class TestClassCodeMapping:
    """Tests for class code to name mapping."""

    def test_class_code_to_name_mapping(self):
        """Test CLASS_CODE_TO_NAME mapping is correct."""
        assert CLASS_CODE_TO_NAME[1] == "IN"
        assert CLASS_CODE_TO_NAME[3] == "CH"
        assert CLASS_CODE_TO_NAME[4] == "HS"
        assert CLASS_CODE_TO_NAME[254] == "NONE"
        assert CLASS_CODE_TO_NAME[255] == "ANY"


class TestGetClassCode:
    """Tests for get_class_code function."""

    def test_get_class_code_valid(self):
        """Test get_class_code with valid classes."""
        assert get_class_code("IN") == 1
        assert get_class_code("CH") == 3
        assert get_class_code("HS") == 4
        assert get_class_code("NONE") == 254
        assert get_class_code("ANY") == 255

    def test_get_class_code_case_insensitive(self):
        """Test get_class_code is case insensitive."""
        assert get_class_code("in") == 1
        assert get_class_code("In") == 1
        assert get_class_code("ch") == 3
        assert get_class_code("Ch") == 3

    def test_get_class_code_invalid(self):
        """Test get_class_code with invalid classes."""
        assert get_class_code("INVALID") is None
        assert get_class_code("") is None
        assert get_class_code("FAKE") is None


class TestGetClassName:
    """Tests for get_class_name function."""

    def test_get_class_name_valid(self):
        """Test get_class_name with valid codes."""
        assert get_class_name(1) == "IN"
        assert get_class_name(3) == "CH"
        assert get_class_name(4) == "HS"
        assert get_class_name(254) == "NONE"
        assert get_class_name(255) == "ANY"

    def test_get_class_name_invalid(self):
        """Test get_class_name with invalid codes."""
        assert get_class_name(99999) is None
        assert get_class_name(-1) is None
        assert get_class_name(2) is None  # 2 is CS (obsolete, not defined)


class TestIsValidClass:
    """Tests for is_valid_class function."""

    def test_valid_named_classes(self):
        """Test is_valid_class with named classes."""
        assert is_valid_class("IN")
        assert is_valid_class("CH")
        assert is_valid_class("HS")
        assert is_valid_class("NONE")
        assert is_valid_class("ANY")

    def test_valid_named_classes_case_insensitive(self):
        """Test is_valid_class is case insensitive for named classes."""
        assert is_valid_class("in")
        assert is_valid_class("In")
        assert is_valid_class("ch")
        assert is_valid_class("hs")

    def test_valid_numeric_classes(self):
        """Test is_valid_class with numeric values."""
        assert is_valid_class("1")
        assert is_valid_class("3")
        assert is_valid_class("4")
        assert is_valid_class("254")
        assert is_valid_class("255")
        assert is_valid_class("0")  # Reserved but valid
        assert is_valid_class("65535")  # Max valid

    def test_valid_class_format(self):
        """Test is_valid_class with CLASS### format."""
        assert is_valid_class("CLASS1")
        assert is_valid_class("CLASS3")
        assert is_valid_class("CLASS256")
        assert is_valid_class("class1")  # Case insensitive

    def test_valid_hex_format(self):
        """Test is_valid_class with hex format."""
        assert is_valid_class("0x1")
        assert is_valid_class("0x3")
        assert is_valid_class("0xFF")
        assert is_valid_class("0X1")  # Case insensitive prefix

    def test_invalid_classes(self):
        """Test is_valid_class with invalid values."""
        assert not is_valid_class("INVALID")
        assert not is_valid_class("")
        assert not is_valid_class("FAKE")
        assert not is_valid_class("-1")
        assert not is_valid_class("65536")  # Out of range
        assert not is_valid_class("0xFFFFFF")  # Out of range
        assert not is_valid_class("CLASS-1")  # Invalid format
        assert not is_valid_class("CLASSabc")  # Invalid format

    def test_whitespace_handling(self):
        """Test is_valid_class handles whitespace."""
        assert is_valid_class(" IN ")
        assert is_valid_class("  1  ")
        assert is_valid_class(" CLASS1 ")


class TestNormalizeClass:
    """Tests for normalize_class function."""

    def test_normalize_named_classes(self):
        """Test normalize_class with named classes."""
        assert normalize_class("IN") == "IN"
        assert normalize_class("CH") == "CH"
        assert normalize_class("HS") == "HS"
        assert normalize_class("NONE") == "NONE"
        assert normalize_class("ANY") == "ANY"

    def test_normalize_case_insensitive(self):
        """Test normalize_class is case insensitive."""
        assert normalize_class("in") == "IN"
        assert normalize_class("In") == "IN"
        assert normalize_class("ch") == "CH"
        assert normalize_class("hs") == "HS"

    def test_normalize_numeric_to_name(self):
        """Test normalize_class converts known numeric to name."""
        assert normalize_class("1") == "IN"
        assert normalize_class("3") == "CH"
        assert normalize_class("4") == "HS"
        assert normalize_class("254") == "NONE"
        assert normalize_class("255") == "ANY"

    def test_normalize_unknown_numeric_to_class_format(self):
        """Test normalize_class converts unknown numeric to CLASS### format."""
        assert normalize_class("2") == "CLASS2"
        assert normalize_class("5") == "CLASS5"
        assert normalize_class("256") == "CLASS256"

    def test_normalize_class_format(self):
        """Test normalize_class handles CLASS### format."""
        assert normalize_class("CLASS1") == "IN"
        assert normalize_class("CLASS3") == "CH"
        assert normalize_class("CLASS256") == "CLASS256"
        assert normalize_class("class1") == "IN"

    def test_normalize_hex_format(self):
        """Test normalize_class handles hex format."""
        assert normalize_class("0x1") == "IN"
        assert normalize_class("0x3") == "CH"
        assert normalize_class("0x4") == "HS"
        assert normalize_class("0xFF") == "ANY"
        assert normalize_class("0x100") == "CLASS256"

    def test_normalize_whitespace(self):
        """Test normalize_class handles whitespace."""
        assert normalize_class(" IN ") == "IN"
        assert normalize_class("  1  ") == "IN"
        assert normalize_class(" CLASS1 ") == "IN"


class TestGetSupportedClasses:
    """Tests for get_supported_classes function."""

    def test_returns_list(self):
        """Test get_supported_classes returns a list."""
        classes = get_supported_classes()
        assert isinstance(classes, list)

    def test_contains_common_classes(self):
        """Test get_supported_classes contains common classes."""
        classes = get_supported_classes()
        assert "IN" in classes
        assert "CH" in classes
        assert "HS" in classes
        assert "NONE" in classes
        assert "ANY" in classes


class TestCommonClasses:
    """Tests for COMMON_CLASSES set."""

    def test_common_classes_contains_in(self):
        """Test COMMON_CLASSES contains IN."""
        assert "IN" in COMMON_CLASSES

    def test_common_classes_contains_ch(self):
        """Test COMMON_CLASSES contains CH."""
        assert "CH" in COMMON_CLASSES

    def test_common_classes_contains_hs(self):
        """Test COMMON_CLASSES contains HS."""
        assert "HS" in COMMON_CLASSES

    def test_common_classes_excludes_query_classes(self):
        """Test COMMON_CLASSES excludes query-only classes."""
        assert "NONE" not in COMMON_CLASSES
        assert "ANY" not in COMMON_CLASSES


class TestProtectedAndUpdatableClasses:
    """Tests for protected and updatable class sets."""

    def test_protected_classes_are_query_only(self):
        """Test that protected classes are query-only."""
        assert "NONE" in PROTECTED_CLASSES
        assert "ANY" in PROTECTED_CLASSES

    def test_protected_classes_not_updatable(self):
        """Test that protected classes are not in updatable classes."""
        for pclass in PROTECTED_CLASSES:
            assert pclass not in UPDATABLE_CLASSES, f"{pclass} should not be updatable"

    def test_common_classes_are_updatable(self):
        """Test that common data classes are updatable."""
        updatable = ["IN", "CH", "HS"]
        for uclass in updatable:
            assert uclass in UPDATABLE_CLASSES, f"{uclass} should be updatable"


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_zero_class(self):
        """Test handling of class 0 (reserved)."""
        assert is_valid_class("0")
        assert normalize_class("0") == "CLASS0"

    def test_max_class(self):
        """Test handling of maximum class value."""
        assert is_valid_class("65535")
        assert normalize_class("65535") == "CLASS65535"

    def test_out_of_range_class(self):
        """Test rejection of out-of-range class values."""
        assert not is_valid_class("65536")
        assert not is_valid_class("100000")
        assert not is_valid_class("-1")

    def test_empty_string(self):
        """Test handling of empty string."""
        assert not is_valid_class("")

    def test_whitespace_only(self):
        """Test handling of whitespace-only string."""
        assert not is_valid_class("   ")

    def test_special_characters(self):
        """Test handling of special characters."""
        assert not is_valid_class("IN!")
        assert not is_valid_class("CH@")
        assert not is_valid_class("1.0")
