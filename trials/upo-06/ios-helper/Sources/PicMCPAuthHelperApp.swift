import AuthenticationServices
import Combine
import CryptoKit
import Foundation
import Security
import SwiftUI
import UIKit

@main
struct PicMCPAuthHelperApp: App {
    @StateObject private var auth = AuthViewModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(auth)
        }
    }
}

struct ContentView: View {
    @EnvironmentObject private var auth: AuthViewModel

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
                    Text("Session ID 由 PicMCP 的 start_pixiv_mobile_auth() 生成，5 分钟内有效且只能使用一次。Helper 不开放匿名创建登录会话的公网入口。")
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
                            Text(auth.isWorking ? "正在等待 Pixiv…" : "连接 Pixiv")
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
                }

                Section {
                    Text("登录页由 Apple 系统安全浏览会话打开。Helper 不接触你的 Pixiv 密码，也不保存 refresh token。它只把一次性 authorization code 与 PKCE verifier 交给 PicMCP，由服务端完成 token exchange。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("PicMCP Auth")
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
final class AuthViewModel: ObservableObject {
    @Published var bridgeURL: String
    @Published var sessionID = ""
    @Published var isWorking = false
    @Published var statusText = "未连接"
    @Published var userID: String?
    @Published var errorMessage: String?

    private let oauth = PixivOAuthSession()
    private let bridge = BridgeClient()

    init() {
        bridgeURL = UserDefaults.standard.string(forKey: "PicMCPBridgeURL") ?? ""
    }

    func connect() async {
        guard !isWorking else { return }
        isWorking = true
        errorMessage = nil
        statusText = "准备登录…"
        defer { isWorking = false }

        do {
            let baseURL = try bridge.validatedBaseURL(bridgeURL)
            UserDefaults.standard.set(baseURL.absoluteString, forKey: "PicMCPBridgeURL")

            let oneTimeSessionID = sessionID.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !oneTimeSessionID.isEmpty else {
                throw BridgeClientError.sessionIDRequired
            }

            statusText = "等待 Pixiv 授权…"
            let authorization = try await oauth.authorize()

            statusText = "交给 PicMCP 完成认证…"
            let result = try await bridge.complete(
                baseURL: baseURL,
                sessionID: oneTimeSessionID,
                code: authorization.code,
                codeVerifier: authorization.codeVerifier
            )

            guard result.status == "CONNECTED" else {
                throw BridgeClientError.serverRejected("PicMCP 没有返回 CONNECTED。")
            }
            userID = result.userID
            sessionID = ""
            statusText = "已连接"
        } catch {
            statusText = "未连接"
            errorMessage = error.localizedDescription
        }
    }
}

struct PixivAuthorization {
    let code: String
    let codeVerifier: String
}

private struct PKCEPreparation {
    let loginURL: URL
    let verifier: String
}

enum PixivOAuthError: LocalizedError {
    case randomGenerationFailed
    case invalidLoginURL
    case sessionDidNotStart
    case callbackMissing
    case callbackInvalid

    var errorDescription: String? {
        switch self {
        case .randomGenerationFailed:
            return "无法生成安全的 PKCE 随机数。"
        case .invalidLoginURL:
            return "无法构造 Pixiv 登录 URL。"
        case .sessionDidNotStart:
            return "Apple 安全浏览会话没有启动。"
        case .callbackMissing:
            return "Pixiv 没有返回登录结果。"
        case .callbackInvalid:
            return "Pixiv 返回的 callback 不包含有效 authorization code。"
        }
    }
}

@MainActor
final class PixivOAuthSession: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var webSession: ASWebAuthenticationSession?

    func authorize() async throws -> PixivAuthorization {
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
                    continuation.resume(throwing: PixivOAuthError.callbackMissing)
                    return
                }
                do {
                    let code = try Self.authorizationCode(from: callbackURL)
                    continuation.resume(
                        returning: PixivAuthorization(
                            code: code,
                            codeVerifier: preparation.verifier
                        )
                    )
                } catch {
                    continuation.resume(throwing: error)
                }
            }

            session.presentationContextProvider = self
            session.prefersEphemeralWebBrowserSession = false
            self.webSession = session

            if !session.start() {
                self.webSession = nil
                continuation.resume(throwing: PixivOAuthError.sessionDidNotStart)
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

    private func makePreparation() throws -> PKCEPreparation {
        let verifier = try Self.randomURLSafeString(byteCount: 32)
        let digest = SHA256.hash(data: Data(verifier.utf8))
        let challenge = Data(digest).base64URLEncodedString()

        var components = URLComponents(string: "https://app-api.pixiv.net/web/v1/login")
        components?.queryItems = [
            URLQueryItem(name: "code_challenge", value: challenge),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "client", value: "pixiv-android")
        ]
        guard let url = components?.url else {
            throw PixivOAuthError.invalidLoginURL
        }
        return PKCEPreparation(loginURL: url, verifier: verifier)
    }

    private static func authorizationCode(from url: URL) throws -> String {
        guard
            url.scheme?.lowercased() == "pixiv",
            url.host?.lowercased() == "account",
            let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
            let code = components.queryItems?.first(where: { $0.name == "code" })?.value,
            !code.isEmpty
        else {
            throw PixivOAuthError.callbackInvalid
        }
        return code
    }

    private static func randomURLSafeString(byteCount: Int) throws -> String {
        var bytes = [UInt8](repeating: 0, count: byteCount)
        let status = bytes.withUnsafeMutableBytes { buffer in
            guard let baseAddress = buffer.baseAddress else {
                return errSecParam
            }
            return SecRandomCopyBytes(kSecRandomDefault, byteCount, baseAddress)
        }
        guard status == errSecSuccess else {
            throw PixivOAuthError.randomGenerationFailed
        }
        return Data(bytes).base64URLEncodedString()
    }
}

private extension Data {
    func base64URLEncodedString() -> String {
        base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

struct BridgeCompleteResponse: Decodable {
    let status: String
    let source: String
    let userID: String?

    enum CodingKeys: String, CodingKey {
        case status
        case source
        case userID = "user_id"
    }
}

enum BridgeClientError: LocalizedError {
    case invalidBaseURL
    case insecureBaseURL
    case invalidResponse
    case sessionIDRequired
    case serverRejected(String)

    var errorDescription: String? {
        switch self {
        case .invalidBaseURL:
            return "PicMCP Bridge URL 无效。"
        case .insecureBaseURL:
            return "PicMCP Bridge 必须使用 HTTPS。"
        case .invalidResponse:
            return "PicMCP Bridge 返回了无效响应。"
        case .sessionIDRequired:
            return "需要 PicMCP 生成的一次性 Session ID。"
        case let .serverRejected(message):
            return message
        }
    }
}

final class BridgeClient {
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func validatedBaseURL(_ raw: String) throws -> URL {
        guard
            let url = URL(string: raw.trimmingCharacters(in: .whitespacesAndNewlines)),
            url.host != nil
        else {
            throw BridgeClientError.invalidBaseURL
        }
        guard url.scheme?.lowercased() == "https" else {
            throw BridgeClientError.insecureBaseURL
        }
        return url
    }

    func complete(
        baseURL: URL,
        sessionID: String,
        code: String,
        codeVerifier: String
    ) async throws -> BridgeCompleteResponse {
        struct Payload: Encodable {
            let sessionID: String
            let code: String
            let codeVerifier: String

            enum CodingKeys: String, CodingKey {
                case sessionID = "session_id"
                case code
                case codeVerifier = "code_verifier"
            }
        }

        var request = URLRequest(url: endpoint(baseURL, "auth/pixiv/mobile/complete"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONEncoder().encode(
            Payload(sessionID: sessionID, code: code, codeVerifier: codeVerifier)
        )
        return try await send(request, as: BridgeCompleteResponse.self)
    }

    private func endpoint(_ baseURL: URL, _ path: String) -> URL {
        path.split(separator: "/").reduce(baseURL) { partial, component in
            partial.appendingPathComponent(String(component))
        }
    }

    private func send<T: Decodable>(_ request: URLRequest, as type: T.Type) async throws -> T {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw BridgeClientError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let message = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            throw BridgeClientError.serverRejected(
                (message?["message"] as? String)
                    ?? "PicMCP Bridge 拒绝了请求（HTTP \(http.statusCode)）。"
            )
        }
        do {
            return try JSONDecoder().decode(type, from: data)
        } catch {
            throw BridgeClientError.invalidResponse
        }
    }
}
