"""IDN (Internationalized Domain Name) and UTF-8 utilities.

Provides functions to:
- Detect and decode punycode (xn--) labels in domain names and zone names
- Decode escaped octal UTF-8 sequences in DNS record data (RDATA)
"""

import re

import idna


def is_punycode_label(label: str) -> bool:
    """Check if a single DNS label is punycode-encoded.

    Args:
        label: A single DNS label (without dots)

    Returns:
        True if the label starts with 'xn--' (case-insensitive)
    """
    return label.lower().startswith("xn--")


def has_punycode(value: str) -> bool:
    """Check if a domain name or value contains any punycode labels.

    Args:
        value: A domain name or DNS value that may contain punycode

    Returns:
        True if any label in the value starts with 'xn--'
    """
    if not value:
        return False

    # Split on dots and check each label
    labels = value.rstrip(".").split(".")
    return any(is_punycode_label(label) for label in labels)


def decode_punycode(value: str) -> str | None:
    """Decode punycode labels in a domain name to UTF-8.

    Args:
        value: A domain name that may contain punycode labels

    Returns:
        The decoded UTF-8 string if punycode was present, None otherwise.
        Returns None if decoding fails.
    """
    if not value or not has_punycode(value):
        return None

    # Preserve trailing dot
    trailing_dot = value.endswith(".")
    value_clean = value.rstrip(".")

    try:
        # Split into labels and decode each punycode label
        labels = value_clean.split(".")
        decoded_labels = []

        for label in labels:
            if is_punycode_label(label):
                # Use idna library for proper IDNA2008 decoding
                try:
                    decoded = idna.decode(label)
                    decoded_labels.append(decoded)
                except idna.core.InvalidCodepoint:
                    # If decoding fails, keep original
                    decoded_labels.append(label)
            else:
                decoded_labels.append(label)

        result = ".".join(decoded_labels)
        if trailing_dot:
            result += "."

        return result

    except Exception:
        # If anything goes wrong, return None
        return None


# Pattern for escaped decimal bytes in DNS: \DDD where DDD is 3 decimal digits
# These represent raw byte values (0-255 in decimal: \000 to \255)
# Per RFC 1035, \DDD is the octet corresponding to the DECIMAL value DDD
DECIMAL_ESCAPE_PATTERN = re.compile(r"\\(\d{3})")


def has_escaped_utf8(value: str) -> bool:
    """Check if a string contains escaped decimal sequences that may be UTF-8.

    DNS zone files and wire format escape non-ASCII bytes as \\DDD where
    DDD is the decimal value of the byte (per RFC 1035). UTF-8 characters
    outside ASCII will appear as sequences like \\231\\189\\145 (网).

    Args:
        value: A string that may contain escaped decimal sequences

    Returns:
        True if the string contains \\DDD escape sequences
    """
    if not value:
        return False
    return bool(DECIMAL_ESCAPE_PATTERN.search(value))


def decode_escaped_utf8(value: str) -> str | None:
    """Decode escaped decimal sequences in a string to UTF-8.

    Converts sequences like \\231\\189\\145\\231\\187\\156 to their
    UTF-8 representation (网络).

    Per RFC 1035, \\DDD represents the byte with decimal value DDD.

    Args:
        value: A string containing escaped decimal byte sequences

    Returns:
        The decoded UTF-8 string if escapes were found and decoded successfully,
        None otherwise.
    """
    if not value or not has_escaped_utf8(value):
        return None

    try:
        # Convert \DDD sequences to raw bytes while preserving other chars
        result_bytes = bytearray()
        i = 0
        while i < len(value):
            # Check for \DDD pattern (backslash followed by 3 digits)
            if i + 3 < len(value) and value[i] == "\\" and value[i + 1 : i + 4].isdigit():
                decimal_str = value[i + 1 : i + 4]
                byte_val = int(decimal_str, 10)  # Parse as DECIMAL per RFC 1035
                if byte_val <= 255:
                    result_bytes.append(byte_val)
                    i += 4
                    continue
            # Regular character - encode as UTF-8
            result_bytes.extend(value[i].encode("utf-8"))
            i += 1

        # Decode the bytes as UTF-8
        decoded = result_bytes.decode("utf-8")

        # Only return if we actually decoded something different
        if decoded != value:
            return decoded
        return None

    except (UnicodeDecodeError, ValueError):
        # If UTF-8 decoding fails, the escapes weren't valid UTF-8
        return None


def get_idn_info(value: str) -> tuple[bool, str | None]:
    """Get IDN information for a domain name (zone name or record label).

    This function is for domain names only, not for RDATA.
    Use get_records_utf8_info() for record data.

    Args:
        value: A domain name to check for punycode

    Returns:
        Tuple of (is_idn, utf8_value) where:
        - is_idn: True if the value contains punycode
        - utf8_value: The decoded UTF-8 string, or None if not IDN
    """
    if not has_punycode(value):
        return (False, None)

    decoded = decode_punycode(value)
    if decoded:
        return (True, decoded)

    # Has punycode but couldn't decode
    return (True, None)


def get_records_utf8_info(records: list[str]) -> tuple[bool, list[str] | None]:
    """Get UTF-8 information for a list of DNS record values (RDATA).

    Detects and decodes escaped decimal UTF-8 sequences in record data.
    For example, "Chinese: \\231\\189\\145\\231\\187\\156" becomes "Chinese: 网络".

    Args:
        records: List of DNS record values

    Returns:
        Tuple of (is_utf8, utf8_values) where:
        - is_utf8: True if any record contains escaped UTF-8
        - utf8_values: List of decoded values (original if no escapes), or None if no UTF-8
    """
    any_utf8 = False
    decoded_records: list[str] = []

    for record in records:
        decoded = decode_escaped_utf8(record)
        if decoded is not None:
            any_utf8 = True
            decoded_records.append(decoded)
        else:
            decoded_records.append(record)

    if any_utf8:
        return (True, decoded_records)

    return (False, None)
