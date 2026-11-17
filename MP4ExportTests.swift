import Foundation

@main struct MP4ExportTests {
    static func command(_ path: String, _ args: [String]) throws -> Data {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: path)
        process.arguments = args
        let pipe = Pipe()
        process.standardOutput = pipe
        try process.run()
        let output = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else { throw NSError(domain: "Test", code: Int(process.terminationStatus)) }
        return output
    }

    static func main() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("mp4-export-test-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let ffmpeg = "/opt/homebrew/bin/ffmpeg"
        let ffprobe = "/opt/homebrew/bin/ffprobe"
        for (name, video, audio, copyVideo, copyAudio) in [
            ("compatible", "libx264", "aac", true, true),
            ("webm", "libvpx-vp9", "libopus", false, false),
            ("mixed", "libx264", "libopus", true, false),
            ("silent", "libx264", "", true, true)
        ] {
            let source = root.appendingPathComponent(name + ".mkv").path
            let output = root.appendingPathComponent(name + ".mp4").path
            var args = ["-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=10:duration=0.5"]
            if !audio.isEmpty { args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.5"] }
            args += ["-c:v", video, "-pix_fmt", "yuv420p"]
            if !audio.isEmpty { args += ["-c:a", audio] }
            args += [source]
            _ = try command(ffmpeg, args)
            let metadata = try command(ffprobe, ["-v", "error", "-show_streams", "-of", "json", source])
            let plan = try MP4Plan(metadata: metadata)
            precondition(plan.copiesVideo == copyVideo && plan.copiesAudio == copyAudio, name)
            _ = try command(ffmpeg, plan.arguments(source: source, output: output))
            let verified = try MP4Plan(metadata: command(ffprobe, ["-v", "error", "-show_streams", "-of", "json", output]))
            precondition(verified.copiesVideo && verified.copiesAudio, name)
            _ = try command(ffmpeg, ["-v", "error", "-i", output, "-f", "null", "-"])
            if copyVideo {
                let original = try command(ffmpeg, ["-v", "error", "-i", source, "-map", "0:v:0", "-c", "copy", "-f", "hash", "-"])
                let exported = try command(ffmpeg, ["-v", "error", "-i", output, "-map", "0:v:0", "-c", "copy", "-f", "hash", "-"])
                precondition(original == exported, "Video must not be recompressed")
            }
            print("PASS: \(name), H.264/AAC MP4 validated and decoded")
        }
    }
}
