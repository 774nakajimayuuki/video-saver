import Foundation

// Local cookie presence is not proof of server-side authentication or a viewing licence.
enum NHKLoginState: Equatable {
    case signedOut
    case needsLogin
    case available(until: Date)

    var title: String {
        switch self {
        case .signedOut: return "このアプリでは未ログインです"
        case .needsLogin: return "ログイン情報の更新が必要です"
        case .available: return "このアプリのログイン情報を確認しました"
        }
    }

    static func inspect(_ cookies: [HTTPCookie], now: Date = Date()) -> NHKLoginState {
        let relevant = eligible(cookies, now: now)
        let kinds = relevant.filter { $0.name == "authz_type" }
        guard kinds.count == 1, kinds[0].value == "acaz" else { return .signedOut }
        let tokens = relevant.filter { $0.name == "z_at" }
        guard tokens.count == 1 else { return .needsLogin }
        let token = tokens[0]
        // Inspect only the expiration field; this does not verify the JWT signature.
        let parts = token.value.split(separator: ".", omittingEmptySubsequences: false)
        guard token.value.utf8.count <= 65536, parts.count == 3,
              parts.allSatisfy({ !$0.isEmpty }) else { return .needsLogin }
        var payload = String(parts[1]).replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        payload += String(repeating: "=", count: (4 - payload.utf8.count % 4) % 4)
        guard let bytes = Data(base64Encoded: payload),
              let claims = try? JSONDecoder().decode(Expiry.self, from: bytes),
              claims.exp.isFinite, claims.exp > now.timeIntervalSince1970 + 30 else { return .needsLogin }
        let expiry = min(Date(timeIntervalSince1970: claims.exp), token.expiresDate ?? .distantFuture)
        guard expiry.timeIntervalSince(now) > 30 else { return .needsLogin }
        return .available(until: expiry)
    }

    private struct Expiry: Decodable { let exp: Double }

    static func accessToken(_ cookies: [HTTPCookie], now: Date = Date()) -> String? {
        guard case .available = inspect(cookies, now: now) else { return nil }
        return eligible(cookies, now: now).first { $0.name == "z_at" }?.value
    }

    private static func eligible(_ cookies: [HTTPCookie], now: Date) -> [HTTPCookie] {
        cookies.filter {
            [".web.nhk", "web.nhk", "www.web.nhk"].contains($0.domain.lowercased()) &&
            $0.path == "/" && $0.isSecure && ($0.expiresDate.map { $0 > now } ?? true)
        }
    }
}
