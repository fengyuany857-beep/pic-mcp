import Foundation
import Security

enum MCPTokenStoreError: LocalizedError {
    case keychain(OSStatus)
    case invalidEncoding

    var errorDescription: String? {
        switch self {
        case let .keychain(status):
            return "无法保存 PicMCP MCP 连接令牌到钥匙串（OSStatus \(status)）。"
        case .invalidEncoding:
            return "钥匙串中的 PicMCP MCP 连接令牌格式无效。"
        }
    }
}

struct MCPTokenStore {
    private let service = "com.picmcp.auth.mcp"
    private let account = "pixiv-read"

    func save(_ token: String) throws {
        guard !token.isEmpty else { throw MCPTokenStoreError.invalidEncoding }
        let data = Data(token.utf8)
        let base = baseQuery()
        SecItemDelete(base as CFDictionary)

        var query = base
        query[kSecValueData as String] = data
        query[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw MCPTokenStoreError.keychain(status)
        }
    }

    func load() throws -> String? {
        var query = baseQuery()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else {
            throw MCPTokenStoreError.keychain(status)
        }
        guard
            let data = result as? Data,
            let token = String(data: data, encoding: .utf8),
            !token.isEmpty
        else {
            throw MCPTokenStoreError.invalidEncoding
        }
        return token
    }

    func exists() -> Bool {
        var query = baseQuery()
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        return SecItemCopyMatching(query as CFDictionary, nil) == errSecSuccess
    }

    private func baseQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }
}
