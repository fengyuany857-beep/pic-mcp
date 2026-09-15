import AuthenticationServices
import Combine
import CryptoKit
import Foundation
import Security
import SwiftUI
import UIKit

@main
struct PicMCPAuthHelperV2App: App {
    @StateObject private var auth = AuthViewModelV2()

    var body: some Scene {
        WindowGroup {
            ContentViewV2()
                .environmentObject(auth)
        }
    }
}

struct ContentViewV2: View {
    @EnvironmentObject private var auth: AuthViewModelV2

    var body: some View {
        NavigationStack {
            Form {
                Section("PicMCP Bridge") {
                    TextField("https://your-picmcp.example", text: $auth.bridgeURL)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                    SecureField("一次性 Session ID", text: $auth.sessionID)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Text("Session ID 仍由 PicMCP Bridge 生成，5 分钟有效且只能使用一次。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("Pixiv") {
                    Button {
                        Task { await auth.connect() }
                    } label: {
                        HStack {
                            if auth.isWorking {
                                ProgressView()
                            } else {
                                Image(systemName: "person.crop.circle.badge.checkmark")
                            }
                            Text(auth.isWorking ? "正在连接 Pixiv…" : "连接 Pixiv")
                        }
                    }
                    .disabled(
                        auth.isWorking
                            || auth.bridgeURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                            || auth.sessionID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    )

                    LabeledContent("状态", value: auth.statusText)
                    if let userID = auth.userID {
                        LabeledContent("Pixiv User ID", value: userID)
                    }
                    if let probe = auth.bookmarksProbe {
                        LabeledContent("Bookmarks Probe", value: probe.status)
                        LabeledContent("公开收藏页", value: String(probe.publicPageCount))
                        LabeledContent("非公开收藏页", value: String(probe.privatePageCount))
                    }
                }

                Section {
                    Text("v2 会在 iPhone 本机完成 Pixiv OAuth token exchange，避开 Cloudflare 出口的 403。refresh token 与 access token 不写入手机存储；它们只通过 HTTPS 交给 PicMCP。Bridge 不回显任何 token。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("PicMCP Auth v2")
            .alert(
                "连接失败",
                isPresented: Binding(
                    get: { auth.errorMessage != nil },
                    set: { if !$0 { auth.errorMessage = nil } }
                )
            ) {
                Button("好", role: .cancel) {}
            } message: {
                Text(auth.errorMessage ?? "")
            }
        }
    }
}

@MainActor
final class AuthViewModelV2: ObservableObject {
    @Published var bridgeURL: String
    @Published var sessionID = ""
    @Published var isWorking = false
    @Published var statusText = "未连接"
    @Published var userID: String?
    @Published var bookmarksProbe: BookmarkProbe?
    @Published var errorMessage: String?

    private let oauth = PixivOAuthSessionV2()
    private let tokenClient = PixivTokenClient()
    private let bridge = BridgeClientV2()

    init() {
        bridgeURL = UserDefaults.standard.string(forKey: "PicMCPBridgeURL") ?? ""
    }

    func connect() async {
        guard !isWorking else { return }
        isWorking = true
        errorMessage = nil
        bookmarksProbe = nil
        statusText = "准备登录…"
        defer { isWorking = false }

        do {
            let baseURL = try bridge.validatedBaseURL(bridgeURL)
            UserDefaults.standard.set(baseURL.absoluteString, forKey: "PicMCPBridgeURL")

            let oneTimeSessionID = sessionID.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !oneTimeSessionID.isEmpty else {
                throw BridgeClientErrorV2.sessionIDRequired
            }

            statusText = "等待 Pixiv 授权…"
            let authorization = try await oauth.authorize()

            statusText = "iPhone 本机交换授权…"
            let token = try await tokenClient.exchange(
                code: authorization.code,
                codeVerifier: authorization.codeVerifier
            )

            statusText = "交给 PicMCP 验证收藏…"
            let result = try await bridge.complete(
                baseURL: baseURL,
                sessionID: oneTimeSessionID,
                token: token
            )

            guard result.status == "CONNECTED" else {
                throw BridgeClientErrorV2.serverRejected("PicMCP 没有返回 CONNECTED。")
            }

            userID = result.userID
            bookmarksProbe = result.bookmarksProbe
            sessionID = ""
            statusText = "已连接"
        } catch {
            statusText = "未连接"
            errorMessage = error.localizedDescription
        }
    }
}

struct PixivAuthorizationV2 {
    let code: String
    let codeVerifier: String
}

private struct PKCEPreparationV2 {
    let loginURL: URL
    let verifier: String
}

enum PixivOAuthErrorV2: LocalizedError {
    case randomGenerationFailed
    case invalidLoginURL
    case sessionDidNotStart
    case callbackMissing
    case callbackInvalid

    var errorDescription: String? {
        switch self {
        case .randomGenerationFailed: return "无法生成安全的 PKCE 随机数。"
        case .invalidLoginURL: return "无法构造 Pixiv 登录 URL。"
        case .sessionDidNotStart: return "Apple 安全浏览会话没有启动。"
        case .callbackMissing: return "Pixiv 没有返回登录结果。"
        case .callbackInvalid: return "Pixiv callback 不包含有效 authorization code。"
        }
    }
}

@MainActor
final class PixivOAuthSessionV2: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var webSession: ASWebAuthenticationSession?

    func authorize() async throws -> PixivAuthorizationV2 {
        let preparation = try makePreparation()
        return try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(
                url: preparation.loginURL,
                callbackURLScheme: "pixiv"
            ) { [weak self] callbackURL, error in
                self?.webSession = nil
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                guard let callbackURL else {
                    continuation.resume(throwing: PixivOAuthErrorV2.callbackMissing)
                    return
                }
                do {
                    let code = try Self.authorizationCode(from: callbackURL)
                    continuation.resume(returning: PixivAuthorizationV2(
                        code: code,
                        codeVerifier: preparation.verifier
                    ))
                } catch {
                    continuation.resume(throwing: error)
                }
            }
            session.presentationContextProvider = self
            session.prefersEphemeralWebBrowserSession = false
            self.webSession = session
            if !session.start() {
                self.webSession = nil
                continuation.resume(throwing: PixivOAuthErrorV2.sessionDidNotStart)
            }
        }
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        _ = session
        for scene in UIApplication.shared.connectedScenes {
            guard let windowScene = scene as? UIWindowScene else { continue }
            if let window = windowScene.windows.first(where: { $0.isKeyWindow }) {
                return window
            }
        }
        return UIWindow(frame: .zero)
    }

    private func makePreparation() throws -> PKCEPreparationV2 {
        let verifier = try Self.randomURLSafeString(byteCount: 32)
        let digest = SHA256.hash(data: Data(verifier.utf8))
        let challenge = Data(digest).base64URLEncodedStringV2()

        var components = URLComponents(string: "https://app-api.pixiv.net/web/v1/login")
        components?.queryItems = [
            URLQueryItem(name: "code_challenge", value: challenge),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "client", value: "pixiv-android")
        ]
        guard let url = components?.url else {
            throw PixivOAuthErrorV2.invalidLoginURL
        }
        return PKCEPreparationV2(loginURL: url, verifier: verifier)
    }

    private static func authorizationCode(from url: URL) throws -> String {
        guard
            url.scheme?.lowercased() == "pixiv",
            url.host?.lowercased() == "account",
            let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
            let code = components.queryItems?.first(where: { $0.name == "code" })?.value,
            !code.isEmpty
        else {
            throw PixivOAuthErrorV2.callbackInvalid
        }
        return code
    }

    private static func randomURLSafeString(byteCount: Int) throws -> String {
        var bytes = [UInt8](repeating: 0, count: byteCount)
        let status = bytes.withUnsafeMutableBytes { buffer in
            guard let baseAddress = buffer.baseAddress else { return errSecParam }
            return SecRandomCopyBytes(kSecRandomDefault, byteCount, baseAddress)
        }
        guard status == errSecSuccess else {
            throw PixivOAuthErrorV2.randomGenerationFailed
        }
        return Data(bytes).base64URLEncodedStringV2()
    }
}

struct PixivLocalToken {
    let accessToken: String
    let refreshToken: String
    let userID: String
}

enum PixivTokenClientError: LocalizedError {
    case invalidResponse
    case rejected(Int, String)
    case missingTokenFields

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            return "Pixiv token endpoint 返回了无效响应。"
        case let .rejected(status, reason):
            return "Pixiv token exchange 失败（HTTP \(status): \(reason)）。"
        case .missingTokenFields:
            return "Pixiv token 响应缺少 access / refresh token 或 user id。"
        }
    }
}

final class PixivTokenClient {
    private let session: URLSession
    private let clientID = "MOBrBDS8blbauoSck0ZfDbtuzpyT"
    private let clientSecret = ["lsACyCD94FhDUt", "GTXi3QzcFE2uU1hqtDaKeqrdwj"].joined()
    private let redirectURI = "https://app-api.pixiv.net/web/v1/users/auth/pixiv/callback"
    private let hashSalt = "28c1fdd170a5204386cb1313c7077b34f83e4aaf4aa829ce78c231e05b0bae2c"
    private let userAgent = "PixivAndroidApp/5.0.166 (Android 10.0; Pixel C)"

    init(session: URLSession = .shared) {
        self.session = session
    }

    func exchange(code: String, codeVerifier: String) async throws -> PixivLocalToken {
        guard let url = URL(string: "https://oauth.secure.pixiv.net/auth/token") else {
            throw PixivTokenClientError.invalidResponse
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.httpBody = formBody([
            "client_id": clientID,
            "client_secret": clientSecret,
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": codeVerifier,
            "redirect_uri": redirectURI,
            "include_policy": "true"
        ])
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        standardHeaders().forEach { request.setValue($0.value, forHTTPHeaderField: $0.key) }

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw PixivTokenClientError.invalidResponse
        }

        let object = try? JSONSerialization.jsonObject(with: data)
        let root = object as? [String: Any]
        let payload = (root?["response"] as? [String: Any]) ?? root

        guard (200..<300).contains(http.statusCode) else {
            let reason = Self.safeReason(root, fallback: "HTTP_\(http.statusCode)")
            throw PixivTokenClientError.rejected(http.statusCode, reason)
        }

        guard
            let accessToken = payload?["access_token"] as? String,
            let refreshToken = payload?["refresh_token"] as? String,
            let user = payload?["user"] as? [String: Any],
            let rawUserID = user["id"]
        else {
            throw PixivTokenClientError.missingTokenFields
        }

        let userID = String(describing: rawUserID)
        guard !accessToken.isEmpty, !refreshToken.isEmpty, !userID.isEmpty else {
            throw PixivTokenClientError.missingTokenFields
        }

        return PixivLocalToken(
            accessToken: accessToken,
            refreshToken: refreshToken,
            userID: userID
        )
    }

    private func standardHeaders() -> [String: String] {
        let time = Self.clientTime()
        return [
            "X-Client-Time": time,
            "X-Client-Hash": Self.md5(time + hashSalt),
            "User-Agent": userAgent,
            "Accept-Language": "zh-CN",
            "App-OS": "Android",
            "App-OS-Version": "Android 10.0",
            "App-Version": "5.0.166"
        ]
    }

    private func formBody(_ values: [String: String]) -> Data? {
        var components = URLComponents()
        components.queryItems = values.map { URLQueryItem(name: $0.key, value: $0.value) }
        return components.percentEncodedQuery?.data(using: .utf8)
    }

    private static func clientTime() -> String {
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.formatOptions = [.withInternetDateTime, .withColonSeparatorInTimeZone]
        return formatter.string(from: Date()).replacingOccurrences(of: "Z", with: "+00:00")
    }

    private static func md5(_ value: String) -> String {
        Insecure.MD5.hash(data: Data(value.utf8))
            .map { String(format: "%02hhx", $0) }
            .joined()
    }

    private static func safeReason(_ root: [String: Any]?, fallback: String) -> String {
        let candidates: [Any?] = [
            root?["error"],
            root?["error_description"],
            (root?["errors"] as? [String: Any])?["system"],
            root?["message"]
        ]
        for candidate in candidates {
            if let text = candidate as? String, !text.isEmpty {
                return String(text.prefix(160)).replacingOccurrences(of: "\n", with: " ")
            }
        }
        return fallback
    }
}

struct BookmarkProbe: Decodable {
    let status: String
    let publicPageCount: Int
    let privatePageCount: Int
    let sampleIDs: [String]
    let errors: [String]

    enum CodingKeys: String, CodingKey {
        case status
        case publicPageCount = "public_page_count"
        case privatePageCount = "private_page_count"
        case sampleIDs = "sample_ids"
        case errors
    }
}

struct BridgeCompleteResponseV2: Decodable {
    let status: String
    let source: String
    let userID: String?
    let bookmarksProbe: BookmarkProbe?

    enum CodingKeys: String, CodingKey {
        case status
        case source
        case userID = "user_id"
        case bookmarksProbe = "bookmarks_probe"
    }
}

enum BridgeClientErrorV2: LocalizedError {
    case invalidBaseURL
    case insecureBaseURL
    case invalidResponse
    case sessionIDRequired
    case serverRejected(String)

    var errorDescription: String? {
        switch self {
        case .invalidBaseURL: return "PicMCP Bridge URL 无效。"
        case .insecureBaseURL: return "PicMCP Bridge 必须使用 HTTPS。"
        case .invalidResponse: return "PicMCP Bridge 返回了无效响应。"
        case .sessionIDRequired: return "需要 PicMCP 生成的一次性 Session ID。"
        case let .serverRejected(message): return message
        }
    }
}

final class BridgeClientV2 {
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func validatedBaseURL(_ raw: String) throws -> URL {
        guard
            let url = URL(string: raw.trimmingCharacters(in: .whitespacesAndNewlines)),
            url.host != nil
        else {
            throw BridgeClientErrorV2.invalidBaseURL
        }
        guard url.scheme?.lowercased() == "https" else {
            throw BridgeClientErrorV2.insecureBaseURL
        }
        return url
    }

    func complete(
        baseURL: URL,
        sessionID: String,
        token: PixivLocalToken
    ) async throws -> BridgeCompleteResponseV2 {
        struct Payload: Encodable {
            let authTransport = "ios_local_exchange_v1"
            let sessionID: String
            let refreshToken: String
            let accessToken: String
            let userID: String

            enum CodingKeys: String, CodingKey {
                case authTransport = "auth_transport"
                case sessionID = "session_id"
                case refreshToken = "refresh_token"
                case accessToken = "access_token"
                case userID = "user_id"
            }
        }

        let endpoint = baseURL
            .appendingPathComponent("auth")
            .appendingPathComponent("pixiv")
            .appendingPathComponent("mobile")
            .appendingPathComponent("complete-client-token")

        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONEncoder().encode(Payload(
            sessionID: sessionID,
            refreshToken: token.refreshToken,
            accessToken: token.accessToken,
            userID: token.userID
        ))

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw BridgeClientErrorV2.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            throw BridgeClientErrorV2.serverRejected(
                (body?["message"] as? String)
                    ?? "PicMCP Bridge 拒绝了请求（HTTP \(http.statusCode)）。"
            )
        }
        do {
            return try JSONDecoder().decode(BridgeCompleteResponseV2.self, from: data)
        } catch {
            throw BridgeClientErrorV2.invalidResponse
        }
    }
}

private extension Data {
    func base64URLEncodedStringV2() -> String {
        base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
