#!/usr/bin/env python3
"""Remove te:trailers header when h2 Chrome fingerprint is active."""

SRC = "/app/camoufox-146.0.1-beta.25/netwerk/protocol/http"


def fix_te_compression():
    """Gate te:trailers in Http2Compression.cpp with MaskConfig."""
    path = f"{SRC}/Http2Compression.cpp"
    with open(path, "r") as f:
        src = f.read()

    old = (
        '  if (addTEHeader && !simpleConnectForm && !isWebsocket) {\n'
        '    // Add in TE: trailers for regular requests\n'
        '    nsAutoCString te("te");\n'
        '    nsAutoCString trailers("trailers");\n'
        '    ProcessHeader(nvPair(te, trailers), false, false);\n'
        '  }'
    )
    new = (
        '  // Camoufox: skip TE: trailers when impersonating Chrome H2\n'
        '  bool skipTeTrailers = MaskConfig::CheckBool("h2:disableTeTrailers");\n'
        '  if (addTEHeader && !simpleConnectForm && !isWebsocket && !skipTeTrailers) {\n'
        '    // Add in TE: trailers for regular requests\n'
        '    nsAutoCString te("te");\n'
        '    nsAutoCString trailers("trailers");\n'
        '    ProcessHeader(nvPair(te, trailers), false, false);\n'
        '  }'
    )
    assert old in src, "te:trailers pattern not found in Http2Compression.cpp"
    src = src.replace(old, new, 1)

    with open(path, "w") as f:
        f.write(src)
    print("FIX te:trailers (Http2Compression.cpp): OK")


def fix_te_transaction():
    """Gate te:trailers in nsHttpTransaction.cpp with MaskConfig."""
    path = f"{SRC}/nsHttpTransaction.cpp"
    with open(path, "r") as f:
        src = f.read()

    # Add include if not present
    if '#include "MaskConfig.hpp"' not in src:
        src = src.replace(
            '#include "nsHttpTransaction.h"',
            '#include "nsHttpTransaction.h"\n#include "MaskConfig.hpp"',
            1,
        )

    old = (
        '    if (NS_FAILED(rv) || !teHeader.Equals("moz_no_te_trailers"_ns)) {\n'
        '      // If the request already has TE:moz_no_te_trailers then\n'
        '      // Http2Compressor::EncodeHeaderBlock won\'t actually add this header.\n'
        '      (void)mRequestHead->SetHeader(nsHttp::TE, "trailers"_ns);\n'
        '    }'
    )
    new = (
        '    if ((NS_FAILED(rv) || !teHeader.Equals("moz_no_te_trailers"_ns))\n'
        '        && !MaskConfig::CheckBool("h2:disableTeTrailers")) {\n'
        '      (void)mRequestHead->SetHeader(nsHttp::TE, "trailers"_ns);\n'
        '    }'
    )
    assert old in src, "te:trailers pattern not found in nsHttpTransaction.cpp"
    src = src.replace(old, new, 1)

    with open(path, "w") as f:
        f.write(src)
    print("FIX te:trailers (nsHttpTransaction.cpp): OK")


if __name__ == "__main__":
    fix_te_compression()
    fix_te_transaction()
    print("\nte:trailers fix applied!")
