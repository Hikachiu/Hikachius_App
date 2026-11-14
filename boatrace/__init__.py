# HTTPS の検証に OS（Windows）の証明書ストアを使う（検証自体は切らない）。
# ウイルス対策ソフト（Norton など）が通信を検査する PC では、サイトの証明書が
# そのソフトの証明書に置き換わるため、certifi だけだと検証に失敗する。
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass
