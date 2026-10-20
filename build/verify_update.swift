// Verify the Sparkle archive against the public key shipped in ReSync.
// This tool never reads or prints a private signing key.
import CryptoKit
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

let args = CommandLine.arguments
guard args.count == 4,
      let signature = Data(base64Encoded: args[2]),
      let keyData = Data(base64Encoded: args[3]) else {
    fail("Usage: verify_update.swift archive signature-base64 public-key-base64")
}
do {
    let key = try Curve25519.Signing.PublicKey(rawRepresentation: keyData)
    let archive = try Data(contentsOf: URL(fileURLWithPath: args[1]), options: .mappedIfSafe)
    guard key.isValidSignature(signature, for: archive) else {
        fail("Sparkle archive signature does not match the embedded public key.")
    }
    print("Sparkle archive signature verified against the embedded public key.")
    let keyHash = SHA256.hash(data: keyData).map { String(format: "%02x", $0) }.joined()
    let archiveHash = SHA256.hash(data: archive).map { String(format: "%02x", $0) }.joined()
    print("Sparkle public key SHA-256: \(keyHash)")
    print("Sparkle archive SHA-256: \(archiveHash)")
} catch {
    fail("Sparkle archive verification failed: \(error.localizedDescription)")
}
