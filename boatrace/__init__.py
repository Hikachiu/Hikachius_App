# 公式サーバーは中間証明書を送ってこないことがあるので、
# certifi ではなく OS の証明書ストアで検証する（検証自体は切らない）
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass
