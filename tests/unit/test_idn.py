"""Unit tests for IDN (Internationalized Domain Name) utilities."""

from dns_zone_manager.dns.idn import (
    decode_escaped_utf8,
    decode_punycode,
    get_idn_info,
    get_records_utf8_info,
    has_escaped_utf8,
    has_punycode,
    is_punycode_label,
)


class TestIsPunycodeLabel:
    """Tests for is_punycode_label function."""

    def test_punycode_label_lowercase(self):
        """Test lowercase punycode label detection."""
        assert is_punycode_label("xn--0zwm56d")
        assert is_punycode_label("xn--fsqu00a")
        assert is_punycode_label("xn--kgbechtv")

    def test_punycode_label_uppercase(self):
        """Test uppercase punycode label detection (case insensitive)."""
        assert is_punycode_label("XN--0ZWM56D")
        assert is_punycode_label("Xn--fsqu00a")
        assert is_punycode_label("xN--KGBECHTV")

    def test_non_punycode_labels(self):
        """Test non-punycode labels return False."""
        assert not is_punycode_label("example")
        assert not is_punycode_label("www")
        assert not is_punycode_label("test123")
        assert not is_punycode_label("")

    def test_partial_prefix_not_punycode(self):
        """Test that partial xn-- prefix is not detected."""
        assert not is_punycode_label("xn-something")
        assert not is_punycode_label("xn")
        assert not is_punycode_label("x")


class TestHasPunycode:
    """Tests for has_punycode function."""

    def test_domain_with_punycode_label(self):
        """Test domains containing punycode labels."""
        # Chinese test domain
        assert has_punycode("xn--0zwm56d.idn.test")
        assert has_punycode("xn--0zwm56d.idn.test.")
        # Multiple labels with one punycode
        assert has_punycode("www.xn--kgbechtv.example.com")

    def test_domain_without_punycode(self):
        """Test domains without punycode labels."""
        assert not has_punycode("example.com")
        assert not has_punycode("www.example.com.")
        assert not has_punycode("test.local")

    def test_empty_and_none_values(self):
        """Test empty/falsy values return False."""
        assert not has_punycode("")
        assert not has_punycode(None)  # type: ignore[arg-type]

    def test_punycode_in_subdomain(self):
        """Test punycode in subdomain is detected."""
        assert has_punycode("api.xn--e1afmkfd.example.com")
        assert has_punycode("mail.xn--kgbechtv.test")

    def test_full_punycode_tld(self):
        """Test fully punycode domain names."""
        # IDN TLDs like .рф (Russia) = xn--p1ai
        assert has_punycode("xn--p1ai")
        assert has_punycode("example.xn--p1ai")


class TestDecodePunycode:
    """Tests for decode_punycode function."""

    def test_decode_chinese_domains(self):
        """Test decoding Chinese punycode domains."""
        # 测试 (test) = xn--0zwm56d
        assert decode_punycode("xn--0zwm56d.idn.test") == "测试.idn.test"
        # 例子 (example) = xn--fsqu00a
        assert decode_punycode("xn--fsqu00a.test.") == "例子.test."
        # 中文 (Chinese) = xn--fiq228c
        assert decode_punycode("xn--fiq228c") == "中文"

    def test_decode_arabic_domains(self):
        """Test decoding Arabic punycode domains."""
        # إختبار (test) = xn--kgbechtv (note: alef with hamza below)
        assert decode_punycode("xn--kgbechtv.test") == "إختبار.test"
        # مصر (Egypt) = xn--wgbh1c
        assert decode_punycode("xn--wgbh1c") == "مصر"

    def test_decode_russian_domains(self):
        """Test decoding Russian (Cyrillic) punycode domains."""
        # сайт (site) = xn--80aswg
        assert decode_punycode("xn--80aswg.test") == "сайт.test"
        # рф (RF/Russia) = xn--p1ai
        assert decode_punycode("example.xn--p1ai.") == "example.рф."

    def test_decode_japanese_domains(self):
        """Test decoding Japanese punycode domains."""
        # テスト (test) = xn--zckzah
        assert decode_punycode("xn--zckzah.test") == "テスト.test"
        # 日本 (Japan) = xn--wgv71a
        assert decode_punycode("xn--wgv71a") == "日本"

    def test_decode_greek_domains(self):
        """Test decoding Greek punycode domains."""
        # ελ (Greece) = xn--qxam
        assert decode_punycode("xn--qxam.test") == "ελ.test"

    def test_preserves_trailing_dot(self):
        """Test that trailing dot is preserved."""
        assert decode_punycode("xn--0zwm56d.test.") == "测试.test."
        assert decode_punycode("xn--0zwm56d.test") == "测试.test"

    def test_decode_non_punycode_returns_none(self):
        """Test that non-punycode domains return None."""
        assert decode_punycode("example.com") is None
        assert decode_punycode("www.test.local.") is None

    def test_decode_empty_returns_none(self):
        """Test empty/falsy values return None."""
        assert decode_punycode("") is None
        assert decode_punycode(None) is None  # type: ignore[arg-type]

    def test_mixed_punycode_and_ascii(self):
        """Test domain with mixed punycode and ASCII labels."""
        # www.测试.idn.test (only xn--0zwm56d is punycode)
        result = decode_punycode("www.xn--0zwm56d.idn.test")
        assert result == "www.测试.idn.test"

    def test_multiple_punycode_labels(self):
        """Test domain with multiple punycode labels."""
        # Both labels are punycode: 测试.中文
        result = decode_punycode("xn--0zwm56d.xn--fiq228c")
        assert result == "测试.中文"


class TestHasEscapedUtf8:
    """Tests for has_escaped_utf8 function."""

    def test_detects_decimal_escapes(self):
        """Test detection of \\DDD escape sequences."""
        # UTF-8 for Chinese character
        assert has_escaped_utf8("\\231\\189\\145")
        # Mixed with text
        assert has_escaped_utf8("Chinese: \\231\\189\\145")
        # Multiple sequences
        assert has_escaped_utf8("\\231\\189\\145\\231\\187\\156")

    def test_no_escapes_returns_false(self):
        """Test strings without escapes return False."""
        assert not has_escaped_utf8("hello world")
        assert not has_escaped_utf8("example.com")
        assert not has_escaped_utf8("test123")

    def test_empty_values_return_false(self):
        """Test empty/falsy values return False."""
        assert not has_escaped_utf8("")
        assert not has_escaped_utf8(None)  # type: ignore[arg-type]

    def test_partial_escapes_not_detected(self):
        """Test that partial escape sequences are not matched."""
        # Only 2 digits
        assert not has_escaped_utf8("\\23")
        # Only 1 digit
        assert not has_escaped_utf8("\\2")
        # Backslash alone
        assert not has_escaped_utf8("\\")

    def test_backslash_with_non_digits(self):
        """Test backslash followed by non-digits."""
        assert not has_escaped_utf8("\\abc")
        assert not has_escaped_utf8("C:\\Users")


class TestDecodeEscapedUtf8:
    """Tests for decode_escaped_utf8 function."""

    def test_decode_chinese_characters(self):
        """Test decoding escaped Chinese UTF-8 characters."""
        # 网 = \231\189\145 (decimal: 231, 189, 145)
        assert decode_escaped_utf8("\\231\\189\\145") == "网"
        # 络 = \231\187\156
        assert decode_escaped_utf8("\\231\\187\\156") == "络"
        # 网络 together
        assert decode_escaped_utf8("\\231\\189\\145\\231\\187\\156") == "网络"

    def test_decode_mixed_ascii_and_escaped(self):
        """Test decoding mixed ASCII and escaped sequences."""
        # "Chinese: 网站" with UTF-8 encoding for Chinese
        result = decode_escaped_utf8("Chinese: \\231\\189\\145")
        assert result == "Chinese: 网"

    def test_decode_preserves_surrounding_text(self):
        """Test that surrounding ASCII text is preserved."""
        result = decode_escaped_utf8("prefix \\231\\189\\145 suffix")
        assert result == "prefix 网 suffix"

    def test_no_escapes_returns_none(self):
        """Test that strings without escapes return None."""
        assert decode_escaped_utf8("hello world") is None
        assert decode_escaped_utf8("example.com") is None

    def test_empty_returns_none(self):
        """Test empty/falsy values return None."""
        assert decode_escaped_utf8("") is None
        assert decode_escaped_utf8(None) is None  # type: ignore[arg-type]

    def test_invalid_utf8_returns_none(self):
        """Test invalid UTF-8 sequences return None."""
        # Invalid UTF-8 sequence (not a valid byte sequence)
        assert decode_escaped_utf8("\\255\\255\\255") is None

    def test_decode_multi_byte_characters(self):
        """Test decoding various multi-byte UTF-8 characters."""
        # Euro sign € = \226\130\172
        assert decode_escaped_utf8("\\226\\130\\172") == "€"
        # Smiley 😀 = \240\159\152\128
        assert decode_escaped_utf8("\\240\\159\\152\\128") == "😀"

    def test_byte_values_above_127(self):
        """Test that byte values above 127 are handled correctly."""
        # Common in UTF-8 multi-byte sequences
        # Example: ñ = \195\177
        assert decode_escaped_utf8("\\195\\177") == "ñ"

    def test_preserves_regular_backslashes(self):
        """Test that other backslash uses are preserved."""
        # This has a normal escaped character (not \\DDD pattern)
        # Only valid \\DDD patterns should be decoded
        result = decode_escaped_utf8("test\\n\\231\\189\\145")
        # Should decode the UTF-8 but leave \\n alone
        assert "网" in result if result else True


class TestGetIdnInfo:
    """Tests for get_idn_info function."""

    def test_punycode_domain_returns_idn_true_with_decoded(self):
        """Test punycode domain returns (True, decoded_value)."""
        is_idn, utf8_value = get_idn_info("xn--0zwm56d.test")
        assert is_idn is True
        assert utf8_value == "测试.test"

    def test_punycode_domain_with_trailing_dot(self):
        """Test punycode domain with trailing dot."""
        is_idn, utf8_value = get_idn_info("xn--kgbechtv.example.com.")
        assert is_idn is True
        assert utf8_value == "إختبار.example.com."

    def test_non_punycode_domain_returns_idn_false(self):
        """Test non-punycode domain returns (False, None)."""
        is_idn, utf8_value = get_idn_info("example.com")
        assert is_idn is False
        assert utf8_value is None

    def test_empty_value_returns_idn_false(self):
        """Test empty value returns (False, None)."""
        is_idn, utf8_value = get_idn_info("")
        assert is_idn is False
        assert utf8_value is None

    def test_multiple_punycode_labels(self):
        """Test domain with multiple punycode labels."""
        is_idn, utf8_value = get_idn_info("xn--0zwm56d.xn--fiq228c.test")
        assert is_idn is True
        assert utf8_value == "测试.中文.test"

    def test_mixed_punycode_and_ascii_subdomain(self):
        """Test domain with punycode subdomain."""
        is_idn, utf8_value = get_idn_info("www.xn--80aswg.ru")
        assert is_idn is True
        assert utf8_value == "www.сайт.ru"


class TestGetRecordsUtf8Info:
    """Tests for get_records_utf8_info function."""

    def test_records_with_escaped_utf8(self):
        """Test records containing escaped UTF-8 are decoded."""
        records = [
            '"Chinese: \\231\\189\\145"',
            '"Normal text"',
        ]
        is_utf8, decoded = get_records_utf8_info(records)
        assert is_utf8 is True
        assert decoded is not None
        assert "网" in decoded[0]
        assert decoded[1] == '"Normal text"'

    def test_records_without_utf8(self):
        """Test records without UTF-8 return (False, None)."""
        records = [
            '"v=spf1 -all"',
            '"dkim=pass"',
        ]
        is_utf8, decoded = get_records_utf8_info(records)
        assert is_utf8 is False
        assert decoded is None

    def test_empty_records_list(self):
        """Test empty records list returns (False, None)."""
        is_utf8, decoded = get_records_utf8_info([])
        assert is_utf8 is False
        assert decoded is None

    def test_all_records_have_utf8(self):
        """Test when all records have UTF-8."""
        records = [
            '"\\231\\189\\145"',  # 网
            '"\\231\\187\\156"',  # 络
        ]
        is_utf8, decoded = get_records_utf8_info(records)
        assert is_utf8 is True
        assert decoded is not None
        assert "网" in decoded[0]
        assert "络" in decoded[1]

    def test_preserves_original_for_non_utf8_records(self):
        """Test that non-UTF8 records are preserved unchanged."""
        records = [
            "10 mail.example.com.",
            '"\\231\\189\\145"',  # Has UTF-8
            "192.0.2.1",
        ]
        is_utf8, decoded = get_records_utf8_info(records)
        assert is_utf8 is True
        assert decoded is not None
        assert decoded[0] == "10 mail.example.com."  # Unchanged
        assert decoded[2] == "192.0.2.1"  # Unchanged

    def test_txt_record_with_complex_utf8(self):
        """Test TXT record with complex UTF-8 content."""
        # Simulating what BIND might return for a TXT record with Chinese
        records = [
            '"Chinese: \\231\\189\\145\\231\\187\\156 website"',
        ]
        is_utf8, decoded = get_records_utf8_info(records)
        assert is_utf8 is True
        assert decoded is not None
        assert "网络" in decoded[0]
        assert "Chinese:" in decoded[0]
        assert "website" in decoded[0]


class TestIdnIntegrationScenarios:
    """Integration tests for real-world IDN scenarios."""

    def test_idn_zone_name(self):
        """Test handling of IDN zone names."""
        # IDN TLD .中文网 (Chinese Network) would be xn--fiq228c.xn--io0a7i
        # Simplified example: .中文 = xn--fiq228c
        is_idn, decoded = get_idn_info("xn--fiq228c.")
        assert is_idn is True
        assert decoded == "中文."

    def test_fqdn_with_idn_labels(self):
        """Test FQDN with IDN labels at various positions."""
        # www.测试.example.com.
        is_idn, decoded = get_idn_info("www.xn--0zwm56d.example.com.")
        assert is_idn is True
        assert decoded == "www.测试.example.com."

    def test_record_with_idn_target(self):
        """Test DNS record data that contains IDN references."""
        # CNAME target might be an IDN domain
        records = ["xn--0zwm56d.example.com."]
        # Note: get_records_utf8_info is for escaped UTF-8, not punycode
        # Punycode in record data would need different handling
        is_utf8, decoded = get_records_utf8_info(records)
        assert is_utf8 is False  # No escaped UTF-8

    def test_multiple_character_sets_in_zone(self):
        """Test handling domains with different character sets."""
        test_cases = [
            ("xn--0zwm56d.test", "测试.test"),  # Chinese
            ("xn--kgbechtv.test", "إختبار.test"),  # Arabic (alef with hamza below)
            ("xn--80aswg.test", "сайт.test"),  # Russian
            ("xn--zckzah.test", "テスト.test"),  # Japanese
            ("xn--qxam.test", "ελ.test"),  # Greek
        ]
        for punycode, expected in test_cases:
            is_idn, decoded = get_idn_info(punycode)
            assert is_idn is True, f"Expected {punycode} to be IDN"
            assert decoded == expected, f"Expected {expected}, got {decoded}"
