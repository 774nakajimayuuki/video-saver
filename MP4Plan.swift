import Foundation

struct MP4Plan {
    struct Metadata: Decodable { let streams: [Stream] }
    struct Stream: Decodable {
        let codec_type: String?
        let codec_name: String?
        let pix_fmt: String?
    }
    let copiesVideo: Bool
    let copiesAudio: Bool

    init(metadata: Data) throws {
        let streams = try JSONDecoder().decode(Metadata.self, from: metadata).streams
        guard let video = streams.first(where: { $0.codec_type == "video" }) else {
            throw NSError(domain: "VideoSaver", code: 2, userInfo: [NSLocalizedDescriptionKey: "映像トラックがありません。"])
        }
        copiesVideo = video.codec_name == "h264" && ["yuv420p", "yuvj420p"].contains(video.pix_fmt ?? "")
        let audio = streams.first(where: { $0.codec_type == "audio" })
        copiesAudio = audio == nil || audio?.codec_name == "aac"
    }

    func arguments(source: String, output: String) -> [String] {
        var args = ["-hide_banner", "-nostdin", "-nostats", "-loglevel", "warning", "-n", "-i", source,
                    "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]
        args += copiesVideo ? ["-c:v", "copy"] : ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"]
        args += copiesAudio ? ["-c:a", "copy"] : ["-c:a", "aac", "-b:a", "192k"]
        args += ["-tag:v", "avc1", "-movflags", "+faststart", "-f", "mp4", output]
        return args
    }
}
