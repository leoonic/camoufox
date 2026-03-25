#!/usr/bin/env python3
"""Apply Brotli fix + H2 + te:trailers to FF148 AFTER network-patches.patch."""

SRC = "/app/camoufox-148.0-beta.1/netwerk/protocol/http"


def fix_brotli():
    path = f"{SRC}/nsHttpHandler.cpp"
    with open(path, "r") as f:
        src = f.read()

    old = ('  if (auto value = MaskConfig::GetString("headers.Accept-Encoding")) {\n'
           '    mHttpsAcceptEncodings.Assign(nsCString(value.value().c_str()));\n'
           '    return NS_OK;\n'
           '  }')
    if old not in src:
        print("Brotli: early-return not found (already fixed?)")
        return

    src = src.replace(old, '', 1)

    old2 = '    mHttpsAcceptEncodings = aAcceptEncodings;'
    idx = src.find(old2)
    if idx >= 0:
        new2 = ('    if (auto value = MaskConfig::GetString("headers.Accept-Encoding")) {\n'
                '      mHttpsAcceptEncodings.Assign(nsCString(value.value().c_str()));\n'
                '    } else {\n'
                '      mHttpsAcceptEncodings = aAcceptEncodings;\n'
                '    }')
        src = src[:idx] + new2 + src[idx + len(old2):]

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
    print("Brotli: FIXED")


def fix_h2():
    path = f"{SRC}/Http2Session.cpp"
    with open(path, "r") as f:
        src = f.read()

    if "cfgHTS" in src:
        print("H2: already patched")
        return

    # Include
    if '#include "MaskConfig.hpp"' not in src:
        src = src.replace('#include "Http2Session.h"',
                          '#include "Http2Session.h"\n#include "MaskConfig.hpp"', 1)

    src = src.replace("static const uint32_t maxSettings = 6;",
                      "static const uint32_t maxSettings = 8;")

    src = src.replace(
        "  uint32_t maxHpackBufferSize = gHttpHandler->DefaultHpackBuffer();",
        '  auto cfgHTS = MaskConfig::GetUint64("h2:headerTableSize");\n'
        '  uint32_t maxHpackBufferSize = cfgHTS\n'
        '      ? static_cast<uint32_t>(cfgHTS.value())\n'
        '      : gHttpHandler->DefaultHpackBuffer();')

    src = src.replace(
        "  // We don't support HTTP/2 Push. Set SETTINGS_TYPE_ENABLE_PUSH to 0\n"
        "  NetworkEndian::writeUint16(packet + kFrameHeaderBytes + (6 * numberOfEntries),\n"
        "                             SETTINGS_TYPE_ENABLE_PUSH);\n"
        "  // The value portion of the setting pair is already initialized to 0\n"
        "  numberOfEntries++;",
        '  auto cfgEP = MaskConfig::GetUint64("h2:enablePush");\n'
        '  NetworkEndian::writeUint16(packet + kFrameHeaderBytes + (6 * numberOfEntries),\n'
        '                             SETTINGS_TYPE_ENABLE_PUSH);\n'
        '  if (cfgEP) {\n'
        '    NetworkEndian::writeUint32(\n'
        '        packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2,\n'
        '        static_cast<uint32_t>(cfgEP.value()));\n'
        '  }\n'
        '  numberOfEntries++;')

    src = src.replace(
        "  if (StaticPrefs::network_http_http2_send_push_max_concurrent_frame()) {\n"
        "    NetworkEndian::writeUint16(\n"
        "        packet + kFrameHeaderBytes + (6 * numberOfEntries),\n"
        "        SETTINGS_TYPE_MAX_CONCURRENT);\n"
        "    // The value portion of the setting pair is already initialized to 0\n"
        "    numberOfEntries++;\n"
        "  }",
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

    src = src.replace(
        "  NetworkEndian::writeUint16(packet + kFrameHeaderBytes + (6 * numberOfEntries),\n"
        "                             SETTINGS_TYPE_INITIAL_WINDOW);\n"
        "  NetworkEndian::writeUint32(\n"
        "      packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2, mPushAllowance);",
        '  auto cfgIW = MaskConfig::GetUint64("h2:initialWindowSize");\n'
        '  uint32_t h2InitWin = cfgIW ? static_cast<uint32_t>(cfgIW.value()) : mPushAllowance;\n'
        '  NetworkEndian::writeUint16(packet + kFrameHeaderBytes + (6 * numberOfEntries),\n'
        '                             SETTINGS_TYPE_INITIAL_WINDOW);\n'
        '  NetworkEndian::writeUint32(\n'
        '      packet + kFrameHeaderBytes + (6 * numberOfEntries) + 2, h2InitWin);')

    if "cfgMHLS" not in src:
        src = src.replace("  bool disableRFC7540Priorities =",
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

    if "cfgWU" not in src:
        src = src.replace(
            "  uint32_t sessionWindowBump = mInitialRwin - kDefaultRwin;",
            '  auto cfgWU = MaskConfig::GetUint64("h2:windowUpdateSize");\n'
            '  if (cfgWU) {\n'
            '    mInitialRwin = static_cast<uint32_t>(cfgWU.value()) + kDefaultRwin;\n'
            '  }\n'
            '  uint32_t sessionWindowBump = mInitialRwin - kDefaultRwin;')

    if "cfgDisablePri" not in src:
        src = src.replace(
            "  if (!disableRFC7540Priorities) {\n"
            "    mUseH2Deps = true;",
            '  bool cfgDisablePri = MaskConfig::CheckBool("h2:disablePriority");\n'
            '  if (!disableRFC7540Priorities && !cfgDisablePri) {\n'
            '    mUseH2Deps = true;')

    with open(path, "w") as f:
        f.write(src)
    print("H2: FIXED")


def fix_h2_compression():
    path = f"{SRC}/Http2Compression.cpp"
    with open(path, "r") as f:
        src = f.read()

    if "MaskConfig" in src:
        print("H2 Compression: already patched")
        return

    src = src.replace('#include "Http2Compression.h"',
                      '#include "Http2Compression.h"\n#include "MaskConfig.hpp"', 1)

    old = ('    ProcessHeader(nvPair(":method"_ns, method), false, false);\n'
           '    ProcessHeader(nvPair(":path"_ns, path), true, false);\n'
           '    ProcessHeader(nvPair(":authority"_ns, host), false, false);\n'
           '    ProcessHeader(nvPair(":scheme"_ns, scheme), false, false);')
    if old in src:
        new = ('    auto cfgOrd = MaskConfig::GetString("h2:pseudoHeaderOrder");\n'
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

    # te:trailers
    old_te = ('  if (addTEHeader && !simpleConnectForm && !isWebsocket) {\n'
              '    // Add in TE: trailers for regular requests\n')
    if old_te in src and "disableTeTrailers" not in src:
        new_te = ('  bool skipTeTrailers = MaskConfig::CheckBool("h2:disableTeTrailers");\n'
                  '  if (addTEHeader && !simpleConnectForm && !isWebsocket && !skipTeTrailers) {\n'
                  '    // Add in TE: trailers for regular requests\n')
        src = src.replace(old_te, new_te, 1)

    with open(path, "w") as f:
        f.write(src)
    print("H2 Compression + te:trailers: FIXED")


def fix_te_transaction():
    path = f"{SRC}/nsHttpTransaction.cpp"
    with open(path, "r") as f:
        src = f.read()

    if "disableTeTrailers" in src:
        print("te:trailers Transaction: already patched")
        return

    if '#include "MaskConfig.hpp"' not in src:
        src = src.replace('#include "nsHttpTransaction.h"',
                          '#include "nsHttpTransaction.h"\n#include "MaskConfig.hpp"', 1)

    old = '    if (NS_FAILED(rv) || !teHeader.Equals("moz_no_te_trailers"_ns)) {'
    if old in src:
        new = ('    if ((NS_FAILED(rv) || !teHeader.Equals("moz_no_te_trailers"_ns))\n'
               '        && !MaskConfig::CheckBool("h2:disableTeTrailers")) {')
        src = src.replace(old, new, 1)
        with open(path, "w") as f:
            f.write(src)
        print("te:trailers Transaction: FIXED")
    else:
        print("te:trailers Transaction: pattern not found")


if __name__ == "__main__":
    fix_brotli()
    fix_h2()
    fix_h2_compression()
    fix_te_transaction()
    print("\nDone!")
