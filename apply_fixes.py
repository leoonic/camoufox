#!/usr/bin/env python3
"""Apply Brotli fix + H2 fingerprint spoofing + H2 pseudo-header order to Firefox source."""

SRC = "/app/camoufox-146.0.1-beta.25/netwerk/protocol/http"

def fix_brotli():
    path = f"{SRC}/nsHttpHandler.cpp"
    with open(path, "r") as f:
        src = f.read()

    old = (
        '  if (auto value = MaskConfig::GetString("headers.Accept-Encoding")) {\n'
        '    mHttpsAcceptEncodings.Assign(nsCString(value.value().c_str()));\n'
        '    return NS_OK;\n'
        '  }\n'
        '  if (isDictionary) {'
    )
    new = '  if (isDictionary) {'

    assert old in src, "Brotli: early-return pattern not found"
    src = src.replace(old, new, 1)

    # Now wrap isSecure and else branches with MaskConfig override
    old2 = (
        '  } else if (isSecure) {\n'
        '    mHttpsAcceptEncodings = aAcceptEncodings;\n'
        '  } else {\n'
        '    // use legacy list if a secure override is not specified\n'
        '    mHttpAcceptEncodings = aAcceptEncodings;'
    )
    new2 = (
        '  } else if (isSecure) {\n'
        '    if (auto value = MaskConfig::GetString("headers.Accept-Encoding")) {\n'
        '      mHttpsAcceptEncodings.Assign(nsCString(value.value().c_str()));\n'
        '    } else {\n'
        '      mHttpsAcceptEncodings = aAcceptEncodings;\n'
        '    }\n'
        '  } else {\n'
        '    if (auto value = MaskConfig::GetString("headers.Accept-Encoding")) {\n'
        '      mHttpAcceptEncodings.Assign(nsCString(value.value().c_str()));\n'
        '    } else {\n'
        '      mHttpAcceptEncodings = aAcceptEncodings;\n'
        '    }'
    )
    assert old2 in src, "Brotli: isSecure/else pattern not found"
    src = src.replace(old2, new2, 1)

    with open(path, "w") as f:
        f.write(src)
    print("FIX 1 (Brotli): OK")


def fix_h2_session():
    path = f"{SRC}/Http2Session.cpp"
    with open(path, "r") as f:
        src = f.read()

    # Include
    src = src.replace(
        '#include "Http2Session.h"',
        '#include "Http2Session.h"\n#include "MaskConfig.hpp"',
        1,
    )

    # maxSettings 6 -> 8
    src = src.replace(
        "static const uint32_t maxSettings = 6;",
        "static const uint32_t maxSettings = 8;",
    )

    # HEADER_TABLE_SIZE configurable
    src = src.replace(
        "  uint32_t maxHpackBufferSize = gHttpHandler->DefaultHpackBuffer();",
        "  auto cfgHTS = MaskConfig::GetUint64(\"h2:headerTableSize\");\n"
        "  uint32_t maxHpackBufferSize = cfgHTS\n"
        "      ? static_cast<uint32_t>(cfgHTS.value())\n"
        "      : gHttpHandler->DefaultHpackBuffer();",
    )

    # ENABLE_PUSH configurable
    src = src.replace(
        "  // We don't support HTTP/2 Push. Set SETTINGS_TYPE_ENABLE_PUSH to 0\n"
        "  NetworkEndian::writeUint16(packet + kFrameHeaderBytes + (6 * numberOfEntries),\n"
        "                             SETTINGS_TYPE_ENABLE_PUSH);\n"
        "  // The value portion of the setting pair is already initialized to 0\n"
        "  numberOfEntries++;",

        "  auto cfgEP = MaskConfig::GetUint64(\"h2:enablePush\");\n"
        "  NetworkEndian::writeUint16(packet + kFrameHeaderBytes + (6 * numberOfEntries),\n"
        "                             SETTINGS_TYPE_ENABLE_PUSH);\n"
        "  if (cfgEP) {\n"
        "    NetworkEndian::writeUint32(\n"
        "        packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2,\n"
        "        static_cast<uint32_t>(cfgEP.value()));\n"
        "  }\n"
        "  numberOfEntries++;",
    )

    # MAX_CONCURRENT configurable
    src = src.replace(
        "  if (StaticPrefs::network_http_http2_send_push_max_concurrent_frame()) {\n"
        "    NetworkEndian::writeUint16(\n"
        "        packet + kFrameHeaderBytes + (6 * numberOfEntries),\n"
        "        SETTINGS_TYPE_MAX_CONCURRENT);\n"
        "    // The value portion of the setting pair is already initialized to 0\n"
        "    numberOfEntries++;\n"
        "  }",

        "  auto cfgMC = MaskConfig::GetUint64(\"h2:maxConcurrentStreams\");\n"
        "  if (cfgMC) {\n"
        "    NetworkEndian::writeUint16(\n"
        "        packet + kFrameHeaderBytes + (6 * numberOfEntries),\n"
        "        SETTINGS_TYPE_MAX_CONCURRENT);\n"
        "    NetworkEndian::writeUint32(\n"
        "        packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2,\n"
        "        static_cast<uint32_t>(cfgMC.value()));\n"
        "    numberOfEntries++;\n"
        "  } else if (StaticPrefs::network_http_http2_send_push_max_concurrent_frame()) {\n"
        "    NetworkEndian::writeUint16(\n"
        "        packet + kFrameHeaderBytes + (6 * numberOfEntries),\n"
        "        SETTINGS_TYPE_MAX_CONCURRENT);\n"
        "    numberOfEntries++;\n"
        "  }",
    )

    # INITIAL_WINDOW configurable
    src = src.replace(
        "  NetworkEndian::writeUint16(packet + kFrameHeaderBytes + (6 * numberOfEntries),\n"
        "                             SETTINGS_TYPE_INITIAL_WINDOW);\n"
        "  NetworkEndian::writeUint32(\n"
        "      packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2, mPushAllowance);",

        "  auto cfgIW = MaskConfig::GetUint64(\"h2:initialWindowSize\");\n"
        "  uint32_t h2InitWin = cfgIW ? static_cast<uint32_t>(cfgIW.value()) : mPushAllowance;\n"
        "  NetworkEndian::writeUint16(packet + kFrameHeaderBytes + (6 * numberOfEntries),\n"
        "                             SETTINGS_TYPE_INITIAL_WINDOW);\n"
        "  NetworkEndian::writeUint32(\n"
        "      packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2, h2InitWin);",
    )

    # Add MAX_HEADER_LIST_SIZE before disableRFC7540Priorities
    src = src.replace(
        "  bool disableRFC7540Priorities =",

        "  // Camoufox: optional MAX_HEADER_LIST_SIZE\n"
        "  auto cfgMHLS = MaskConfig::GetUint64(\"h2:maxHeaderListSize\");\n"
        "  if (cfgMHLS) {\n"
        "    NetworkEndian::writeUint16(\n"
        "        packet + kFrameHeaderBytes + (6 * numberOfEntries), 6);\n"
        "    NetworkEndian::writeUint32(\n"
        "        packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2,\n"
        "        static_cast<uint32_t>(cfgMHLS.value()));\n"
        "    numberOfEntries++;\n"
        "  }\n"
        "\n"
        "  bool disableRFC7540Priorities =",
        1,
    )

    # Configurable WINDOW_UPDATE
    src = src.replace(
        "  // now bump the local session window from 64KB\n"
        "  uint32_t sessionWindowBump = mInitialRwin - kDefaultRwin;",

        "  // Camoufox: configurable WINDOW_UPDATE size\n"
        "  auto cfgWU = MaskConfig::GetUint64(\"h2:windowUpdateSize\");\n"
        "  if (cfgWU) {\n"
        "    mInitialRwin = static_cast<uint32_t>(cfgWU.value()) + kDefaultRwin;\n"
        "  }\n"
        "  uint32_t sessionWindowBump = mInitialRwin - kDefaultRwin;",
    )

    # Disable priority option
    src = src.replace(
        "  if (!disableRFC7540Priorities) {\n"
        "    mUseH2Deps = true;",

        "  bool cfgDisablePri = MaskConfig::GetBool(\"h2:disablePriority\", false);\n"
        "  if (!disableRFC7540Priorities && !cfgDisablePri) {\n"
        "    mUseH2Deps = true;",
    )

    with open(path, "w") as f:
        f.write(src)
    print("FIX 2 (H2 Session): OK")


def fix_h2_compression():
    path = f"{SRC}/Http2Compression.cpp"
    with open(path, "r") as f:
        src = f.read()

    # Include
    src = src.replace(
        '#include "Http2Compression.h"',
        '#include "Http2Compression.h"\n#include "MaskConfig.hpp"',
        1,
    )

    # Pseudo-header order
    old = (
        "  // colon headers first\n"
        "  if (!simpleConnectForm) {\n"
        '    ProcessHeader(nvPair(":method"_ns, method), false, false);\n'
        '    ProcessHeader(nvPair(":path"_ns, path), true, false);\n'
        '    ProcessHeader(nvPair(":authority"_ns, host), false, false);\n'
        '    ProcessHeader(nvPair(":scheme"_ns, scheme), false, false);'
    )
    new = (
        "  // colon headers first\n"
        "  if (!simpleConnectForm) {\n"
        '    // Camoufox: configurable pseudo-header order\n'
        '    auto cfgOrd = MaskConfig::GetString("h2:pseudoHeaderOrder");\n'
        '    std::string h2Ord = cfgOrd ? cfgOrd.value() : "mpas";\n'
        '    for (char c : h2Ord) {\n'
        '      switch (c) {\n'
        '        case \'m\': ProcessHeader(nvPair(":method"_ns, method), false, false); break;\n'
        '        case \'p\': ProcessHeader(nvPair(":path"_ns, path), true, false); break;\n'
        '        case \'a\': ProcessHeader(nvPair(":authority"_ns, host), false, false); break;\n'
        '        case \'s\': ProcessHeader(nvPair(":scheme"_ns, scheme), false, false); break;\n'
        '      }\n'
        '    }'
    )
    assert old in src, "H2 Compression: pseudo-header pattern not found"
    src = src.replace(old, new, 1)

    with open(path, "w") as f:
        f.write(src)
    print("FIX 3 (H2 Pseudo-headers): OK")


if __name__ == "__main__":
    fix_brotli()
    fix_h2_session()
    fix_h2_compression()
    print("\nAll 3 fixes applied!")
