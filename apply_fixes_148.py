#!/usr/bin/env python3
"""Apply H2 fingerprint + te:trailers fixes to FF148 source."""

SRC = "/app/camoufox-148.0/netwerk/protocol/http"


def fix_brotli():
    """Fix Brotli Accept-Encoding override."""
    path = f"{SRC}/nsHttpHandler.cpp"
    with open(path, "r") as f:
        src = f.read()

    # Check if the buggy pattern exists (from network-patches.patch)
    if 'MaskConfig::GetString("headers.Accept-Encoding")' not in src:
        print("FIX Brotli: MaskConfig Accept-Encoding not found (patch may not have applied)")
        return

    # Find the SetAcceptEncodings function with the buggy early return
    old = '  if (auto value = MaskConfig::GetString("headers.Accept-Encoding")) {\n    mHttpsAcceptEncodings.Assign(nsCString(value.value().c_str()));\n    return NS_OK;\n  }'
    if old not in src:
        print("FIX Brotli: early-return pattern not found (may already be fixed)")
        return

    # Remove the early return, we'll add it inside the branches
    src = src.replace(old, '', 1)

    # Now add the override inside isSecure and else branches
    old2 = '    mHttpsAcceptEncodings = aAcceptEncodings;'
    new2 = ('    if (auto value = MaskConfig::GetString("headers.Accept-Encoding")) {\n'
            '      mHttpsAcceptEncodings.Assign(nsCString(value.value().c_str()));\n'
            '    } else {\n'
            '      mHttpsAcceptEncodings = aAcceptEncodings;\n'
            '    }')

    # Find and replace the first occurrence (isSecure branch)
    idx = src.find(old2)
    if idx >= 0:
        src = src[:idx] + new2 + src[idx + len(old2):]

    # Find the else branch (mHttpAcceptEncodings)
    old3 = '    mHttpAcceptEncodings = aAcceptEncodings;'
    idx = src.find(old3)
    if idx >= 0:
        new3 = ('    if (auto value = MaskConfig::GetString("headers.Accept-Encoding")) {\n'
                '      mHttpAcceptEncodings.Assign(nsCString(value.value().c_str()));\n'
                '    } else {\n'
                '      mHttpAcceptEncodings = aAcceptEncodings;\n'
                '    }')
        src = src[:idx] + new3 + src[idx + len(old3):]

    with open(path, "w") as f:
        f.write(src)
    print("FIX Brotli: OK")


def fix_h2_session():
    """Add H2 fingerprint spoofing to Http2Session.cpp."""
    path = f"{SRC}/Http2Session.cpp"
    with open(path, "r") as f:
        src = f.read()

    if "MaskConfig" in src:
        print("FIX H2 Session: already patched")
        return

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
    old = "  uint32_t maxHpackBufferSize = gHttpHandler->DefaultHpackBuffer();"
    if old in src:
        src = src.replace(old,
            '  auto cfgHTS = MaskConfig::GetUint64("h2:headerTableSize");\n'
            '  uint32_t maxHpackBufferSize = cfgHTS\n'
            '      ? static_cast<uint32_t>(cfgHTS.value())\n'
            '      : gHttpHandler->DefaultHpackBuffer();')

    # ENABLE_PUSH configurable
    old = ("  // We don't support HTTP/2 Push. Set SETTINGS_TYPE_ENABLE_PUSH to 0\n"
           "  NetworkEndian::writeUint16(packet + kFrameHeaderBytes + (6 * numberOfEntries),\n"
           "                             SETTINGS_TYPE_ENABLE_PUSH);\n"
           "  // The value portion of the setting pair is already initialized to 0\n"
           "  numberOfEntries++;")
    if old in src:
        src = src.replace(old,
            '  auto cfgEP = MaskConfig::GetUint64("h2:enablePush");\n'
            '  NetworkEndian::writeUint16(packet + kFrameHeaderBytes + (6 * numberOfEntries),\n'
            '                             SETTINGS_TYPE_ENABLE_PUSH);\n'
            '  if (cfgEP) {\n'
            '    NetworkEndian::writeUint32(\n'
            '        packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2,\n'
            '        static_cast<uint32_t>(cfgEP.value()));\n'
            '  }\n'
            '  numberOfEntries++;')

    # MAX_CONCURRENT configurable
    old = ("  if (StaticPrefs::network_http_http2_send_push_max_concurrent_frame()) {\n"
           "    NetworkEndian::writeUint16(\n"
           "        packet + kFrameHeaderBytes + (6 * numberOfEntries),\n"
           "        SETTINGS_TYPE_MAX_CONCURRENT);\n"
           "    // The value portion of the setting pair is already initialized to 0\n"
           "    numberOfEntries++;\n"
           "  }")
    if old in src:
        src = src.replace(old,
            '  auto cfgMC = MaskConfig::GetUint64("h2:maxConcurrentStreams");\n'
            '  if (cfgMC) {\n'
            '    NetworkEndian::writeUint16(\n'
            '        packet + kFrameHeaderBytes + (6 * numberOfEntries),\n'
            '        SETTINGS_TYPE_MAX_CONCURRENT);\n'
            '    NetworkEndian::writeUint32(\n'
            '        packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2,\n'
            '        static_cast<uint32_t>(cfgMC.value()));\n'
            '    numberOfEntries++;\n'
            '  } else if (StaticPrefs::network_http_http2_send_push_max_concurrent_frame()) {\n'
            '    NetworkEndian::writeUint16(\n'
            '        packet + kFrameHeaderBytes + (6 * numberOfEntries),\n'
            '        SETTINGS_TYPE_MAX_CONCURRENT);\n'
            '    numberOfEntries++;\n'
            '  }')

    # INITIAL_WINDOW configurable
    old = ("  NetworkEndian::writeUint16(packet + kFrameHeaderBytes + (6 * numberOfEntries),\n"
           "                             SETTINGS_TYPE_INITIAL_WINDOW);\n"
           "  NetworkEndian::writeUint32(\n"
           "      packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2, mPushAllowance);")
    if old in src:
        src = src.replace(old,
            '  auto cfgIW = MaskConfig::GetUint64("h2:initialWindowSize");\n'
            '  uint32_t h2InitWin = cfgIW ? static_cast<uint32_t>(cfgIW.value()) : mPushAllowance;\n'
            '  NetworkEndian::writeUint16(packet + kFrameHeaderBytes + (6 * numberOfEntries),\n'
            '                             SETTINGS_TYPE_INITIAL_WINDOW);\n'
            '  NetworkEndian::writeUint32(\n'
            '      packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2, h2InitWin);')

    # MAX_HEADER_LIST_SIZE
    old = "  bool disableRFC7540Priorities ="
    if old in src and "cfgMHLS" not in src:
        src = src.replace(old,
            '  auto cfgMHLS = MaskConfig::GetUint64("h2:maxHeaderListSize");\n'
            '  if (cfgMHLS) {\n'
            '    NetworkEndian::writeUint16(\n'
            '        packet + kFrameHeaderBytes + (6 * numberOfEntries), 6);\n'
            '    NetworkEndian::writeUint32(\n'
            '        packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2,\n'
            '        static_cast<uint32_t>(cfgMHLS.value()));\n'
            '    numberOfEntries++;\n'
            '  }\n\n'
            '  bool disableRFC7540Priorities =', 1)

    # WINDOW_UPDATE
    old = "  uint32_t sessionWindowBump = mInitialRwin - kDefaultRwin;"
    if old in src and "cfgWU" not in src:
        src = src.replace(old,
            '  auto cfgWU = MaskConfig::GetUint64("h2:windowUpdateSize");\n'
            '  if (cfgWU) {\n'
            '    mInitialRwin = static_cast<uint32_t>(cfgWU.value()) + kDefaultRwin;\n'
            '  }\n'
            '  uint32_t sessionWindowBump = mInitialRwin - kDefaultRwin;')

    # Disable priority
    old = ("  if (!disableRFC7540Priorities) {\n"
           "    mUseH2Deps = true;")
    if old in src and "cfgDisablePri" not in src:
        src = src.replace(old,
            '  bool cfgDisablePri = MaskConfig::CheckBool("h2:disablePriority");\n'
            '  if (!disableRFC7540Priorities && !cfgDisablePri) {\n'
            '    mUseH2Deps = true;')

    with open(path, "w") as f:
        f.write(src)
    print("FIX H2 Session: OK")


def fix_h2_compression():
    """Add pseudo-header order to Http2Compression.cpp."""
    path = f"{SRC}/Http2Compression.cpp"
    with open(path, "r") as f:
        src = f.read()

    if "MaskConfig" in src:
        print("FIX H2 Compression: already patched")
        return

    src = src.replace(
        '#include "Http2Compression.h"',
        '#include "Http2Compression.h"\n#include "MaskConfig.hpp"',
        1,
    )

    old = ('  if (!simpleConnectForm) {\n'
           '    ProcessHeader(nvPair(":method"_ns, method), false, false);\n'
           '    ProcessHeader(nvPair(":path"_ns, path), true, false);\n'
           '    ProcessHeader(nvPair(":authority"_ns, host), false, false);\n'
           '    ProcessHeader(nvPair(":scheme"_ns, scheme), false, false);')
    if old in src:
        new = ('  if (!simpleConnectForm) {\n'
               '    auto cfgOrd = MaskConfig::GetString("h2:pseudoHeaderOrder");\n'
               '    std::string h2Ord = cfgOrd ? cfgOrd.value() : "mpas";\n'
               '    for (char c : h2Ord) {\n'
               '      switch (c) {\n'
               "        case 'm': ProcessHeader(nvPair(\":method\"_ns, method), false, false); break;\n"
               "        case 'p': ProcessHeader(nvPair(\":path\"_ns, path), true, false); break;\n"
               "        case 'a': ProcessHeader(nvPair(\":authority\"_ns, host), false, false); break;\n"
               "        case 's': ProcessHeader(nvPair(\":scheme\"_ns, scheme), false, false); break;\n"
               '      }\n'
               '    }')
        src = src.replace(old, new, 1)

    with open(path, "w") as f:
        f.write(src)
    print("FIX H2 Compression: OK")


def fix_te_trailers():
    """Remove te:trailers header."""
    # Http2Compression.cpp
    path = f"{SRC}/Http2Compression.cpp"
    with open(path, "r") as f:
        src = f.read()

    old = ('  if (addTEHeader && !simpleConnectForm && !isWebsocket) {\n'
           '    // Add in TE: trailers for regular requests\n')
    if old in src and "disableTeTrailers" not in src:
        new = ('  bool skipTeTrailers = MaskConfig::CheckBool("h2:disableTeTrailers");\n'
               '  if (addTEHeader && !simpleConnectForm && !isWebsocket && !skipTeTrailers) {\n'
               '    // Add in TE: trailers for regular requests\n')
        src = src.replace(old, new, 1)
        with open(path, "w") as f:
            f.write(src)
        print("FIX te:trailers (Compression): OK")
    else:
        print("FIX te:trailers (Compression): pattern not found or already applied")

    # nsHttpTransaction.cpp
    path = f"{SRC}/nsHttpTransaction.cpp"
    with open(path, "r") as f:
        src = f.read()

    if '#include "MaskConfig.hpp"' not in src:
        src = src.replace(
            '#include "nsHttpTransaction.h"',
            '#include "nsHttpTransaction.h"\n#include "MaskConfig.hpp"',
            1,
        )

    old = '    if (NS_FAILED(rv) || !teHeader.Equals("moz_no_te_trailers"_ns)) {'
    if old in src and "disableTeTrailers" not in src:
        new = ('    if ((NS_FAILED(rv) || !teHeader.Equals("moz_no_te_trailers"_ns))\n'
               '        && !MaskConfig::CheckBool("h2:disableTeTrailers")) {')
        src = src.replace(old, new, 1)
        with open(path, "w") as f:
            f.write(src)
        print("FIX te:trailers (Transaction): OK")
    else:
        print("FIX te:trailers (Transaction): pattern not found or already applied")


if __name__ == "__main__":
    fix_brotli()
    fix_h2_session()
    fix_h2_compression()
    fix_te_trailers()
    print("\nAll fixes applied to FF148!")
