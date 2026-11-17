import Foundation

@main struct NHKLoginStateTests {
    static func main() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        func cookie(_ name: String, _ value: String, domain: String = ".web.nhk", path: String = "/", secure: Bool = true, expires: Date? = nil) -> HTTPCookie {
            var properties: [HTTPCookiePropertyKey: Any] = [.name: name, .value: value, .domain: domain, .path: path]
            if secure { properties[.secure] = "TRUE" }
            if let expires { properties[.expires] = expires }
            return HTTPCookie(properties: properties)!
        }
        func token(_ json: String) -> String {
            let value = Data(json.utf8).base64EncodedString().replacingOccurrences(of: "+", with: "-")
                .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
            return "synthetic.\(value).synthetic"
        }
        let good = token("{\"exp\":1800003600}")
        let kind = cookie("authz_type", "acaz")
        let access = cookie("z_at", good)
        func check(_ name: String, _ cookies: [HTTPCookie], _ expected: NHKLoginState) {
            precondition(NHKLoginState.inspect(cookies, now: now) == expected, name)
            print("PASS: \(name)")
        }
        check("empty session", [], .signedOut)
        check("account session", [kind, access], .available(until: now.addingTimeInterval(3600)))
        check("accountless session is not login", [cookie("authz_type", "r-alaz"), access], .signedOut)
        check("unrelated host", [cookie("authz_type", "acaz", domain: ".web.nhk.evil.example"), access], .signedOut)
        check("wrong cookie path", [kind, cookie("z_at", good, path: "/other")], .needsLogin)
        check("insecure token", [kind, cookie("z_at", good, secure: false)], .needsLogin)
        check("missing token", [kind], .needsLogin)
        check("expired JWT", [kind, cookie("z_at", token("{\"exp\":1799999999}"))], .needsLogin)
        check("about to expire", [kind, cookie("z_at", token("{\"exp\":1800000029}"))], .needsLogin)
        check("invalid JWT", [kind, cookie("z_at", "not-a-jwt")], .needsLogin)
        check("missing expiration", [kind, cookie("z_at", token("{}"))], .needsLogin)
        check("boolean expiration", [kind, cookie("z_at", token("{\"exp\":true}"))], .needsLogin)
        check("expired cookie", [kind, cookie("z_at", good, expires: now.addingTimeInterval(-1))], .needsLogin)
        check("cookie expiry limits availability", [kind, cookie("z_at", good, expires: now.addingTimeInterval(60))], .available(until: now.addingTimeInterval(60)))
        check("ambiguous duplicate tokens", [kind, access, cookie("z_at", good, domain: "www.web.nhk")], .needsLogin)
        check("ambiguous auth type", [kind, cookie("authz_type", "r-alaz", domain: "www.web.nhk"), access], .signedOut)
        precondition(NHKLoginState.accessToken([kind, access], now: now) == good)
        precondition(NHKLoginState.accessToken([kind, access, cookie("z_at", good, domain: "www.web.nhk")], now: now) == nil)
        precondition(NHKLoginState.accessToken([kind, cookie("z_at", good, domain: ".evil.example")], now: now) == nil)
        print("PASS: credential selection and ambiguity boundaries")
    }
}
