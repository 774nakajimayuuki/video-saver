// Controlled clear-video test. Fixture is generated locally, contains no NHK data.
#define CDM_PROBE_NO_MAIN
#include "cdm_probe.cpp"
#include <fstream>
#include <stdexcept>

class Frame final : public cdm::VideoFrame {
 public:
  ~Frame() override { if (buffer_) buffer_->Destroy(); }
  void SetFormat(cdm::VideoFormat format) override { format_ = format; }
  cdm::VideoFormat Format() const override { return format_; }
  void SetSize(cdm::Size size) override { size_ = size; }
  cdm::Size Size() const override { return size_; }
  void SetFrameBuffer(cdm::Buffer* buffer) override {
    if (buffer_ && buffer_ != buffer) buffer_->Destroy();
    buffer_ = buffer;
  }
  cdm::Buffer* FrameBuffer() override { return buffer_; }
  void SetPlaneOffset(cdm::VideoPlane plane, uint32_t value) override { if (plane < 3) offsets_[plane] = value; }
  uint32_t PlaneOffset(cdm::VideoPlane plane) override { return plane < 3 ? offsets_[plane] : 0; }
  void SetStride(cdm::VideoPlane plane, uint32_t value) override { if (plane < 3) strides_[plane] = value; }
  uint32_t Stride(cdm::VideoPlane plane) override { return plane < 3 ? strides_[plane] : 0; }
  void SetTimestamp(int64_t value) override { timestamp_ = value; }
  int64_t Timestamp() const override { return timestamp_; }

  bool write(std::ofstream& output, uint32_t width, uint32_t height) {
    if (!buffer_ || (format_ != cdm::kI420 && format_ != cdm::kYv12)
        || size_.width != static_cast<int32_t>(width) || size_.height != static_cast<int32_t>(height)) return false;
    for (unsigned plane = 0; plane < 3; ++plane) {
      const uint32_t cols = plane == 0 ? width : (width + 1) / 2;
      const uint32_t rows = plane == 0 ? height : (height + 1) / 2;
      if (strides_[plane] < cols || rows == 0) return false;
      const uint64_t end = uint64_t(offsets_[plane]) + uint64_t(rows - 1) * strides_[plane] + cols;
      if (end > buffer_->Capacity()) return false;
      for (uint32_t row = 0; row < rows; ++row) {
        output.write(reinterpret_cast<const char*>(buffer_->Data() + offsets_[plane] + row * strides_[plane]), cols);
      }
    }
    return output.good();
  }
 private:
  cdm::VideoFormat format_ = cdm::kUnknownVideoFormat;
  cdm::Size size_{};
  cdm::Buffer* buffer_ = nullptr;
  uint32_t offsets_[3]{}, strides_[3]{};
  int64_t timestamp_ = 0;
};

template<class T> T read(std::ifstream& input) {
  T value{};
  if (!input.read(reinterpret_cast<char*>(&value), sizeof value)) throw std::runtime_error("fixture truncated");
  return value;
}
static std::vector<uint8_t> read_bytes(std::ifstream& input, uint32_t size) {
  if (size > 16u * 1024u * 1024u) throw std::runtime_error("fixture too large");
  std::vector<uint8_t> bytes(size);
  if (size && !input.read(reinterpret_cast<char*>(bytes.data()), size)) throw std::runtime_error("fixture truncated");
  return bytes;
}

static int decode(cdm::ContentDecryptionModule_10* module,
                  Host<cdm::Host_10, cdm::ContentDecryptionModule_10>& host,
                  const char* fixture_path, const char* output_path, bool encrypted_config) {
  std::ifstream input(fixture_path, std::ios::binary);
  if (read<uint64_t>(input) != 0x45424f52504d4443ull) return 30; // CDMPROBE
  const auto width = read<uint32_t>(input), height = read<uint32_t>(input);
  const auto codec = read<uint32_t>(input), profile = read<uint32_t>(input);
  auto extra = read_bytes(input, read<uint32_t>(input));
  const auto count = read<uint32_t>(input);
  if (width == 0 || width > 1920 || height == 0 || height > 1080 || count > 240) return 30;
  cdm::VideoDecoderConfig_2 config{};
  config.codec = static_cast<cdm::VideoCodec>(codec);
  config.profile = static_cast<cdm::VideoCodecProfile>(profile);
  config.format = cdm::kI420;
  config.coded_size = {static_cast<int32_t>(width), static_cast<int32_t>(height)};
  config.extra_data = extra.empty() ? nullptr : extra.data();
  config.extra_data_size = static_cast<uint32_t>(extra.size());
  config.encryption_scheme = encrypted_config ? cdm::EncryptionScheme::kCenc : cdm::EncryptionScheme::kUnencrypted;
  event("decoder_initialize_begin");
  auto status = module->InitializeVideoDecoder(config);
  event("decoder_initialize_status", status);
  if (status == cdm::kDeferredInitialization) {
    auto deadline = Clock::now() + std::chrono::seconds(5);
    while (host.decoder_initialized.load() < 0 && Clock::now() < deadline) {
      host.pump(); std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    status = static_cast<cdm::Status>(host.decoder_initialized.load());
  }
  if (status != cdm::kSuccess) return 31;
  std::ofstream output(output_path, std::ios::binary);
  if (!output) { module->DeinitializeDecoder(cdm::kStreamTypeVideo); return 32; }
  uint32_t frames = 0;
  int result = 0;
  for (uint32_t index = 0; index < count + 32; ++index) {
    cdm::InputBuffer_2 buffer{};
    std::vector<uint8_t> data;
    if (index < count) {
      buffer.timestamp = read<int64_t>(input);
      data = read_bytes(input, read<uint32_t>(input));
      buffer.data = data.data(); buffer.data_size = static_cast<uint32_t>(data.size());
    }
    buffer.encryption_scheme = cdm::EncryptionScheme::kUnencrypted;
    Frame frame;
    status = module->DecryptAndDecodeFrame(buffer, &frame);
    if (status == cdm::kSuccess) {
      if (!frame.write(output, width, height)) { event("frame_layout_invalid"); result = 33; break; }
      ++frames;
    } else if (status != cdm::kNeedMoreData) {
      event("decode_error_status", status); result = 34; break;
    } else if (index >= count) break;
  }
  module->DeinitializeDecoder(cdm::kStreamTypeVideo);
  output.close();
  event("decoded_frames", frames); event("expected_frames", count);
  event("width", width); event("height", height);
  return result != 0 ? result : frames == count ? 0 : 35;
}

int main(int argc, char** argv) {
  if (argc != 4 && argc != 5) return 2;
  const bool encrypted_config = argc == 5 && std::string(argv[4]) == "cenc-config";
  void* library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  if (!library) { event("load_failed"); return 10; }
  auto init = reinterpret_cast<void (*)()>(dlsym(library, "InitializeCdmModule_4"));
  auto deinit = reinterpret_cast<void (*)()>(dlsym(library, "DeinitializeCdmModule"));
  auto create = reinterpret_cast<Create>(dlsym(library, "CreateCdmInstance"));
  if (!init || !deinit || !create) return 11;
  init();
  Host<cdm::Host_10, cdm::ContentDecryptionModule_10> host;
  constexpr char system[] = "com.widevine.alpha";
  host.module = static_cast<cdm::ContentDecryptionModule_10*>(create(10, system, sizeof(system)-1, &decltype(host)::get, &host));
  if (!host.module) { deinit(); dlclose(library); return 20; }
  host.module->Initialize(false, false, false);
  auto deadline = Clock::now() + std::chrono::seconds(10);
  while (host.initialized.load() < 0 && Clock::now() < deadline) {
    host.pump(); std::this_thread::sleep_for(std::chrono::milliseconds(5));
  }
  int result = 21;
  if (host.initialized.load() == 1) {
    try { result = decode(host.module, host, argv[2], argv[3], encrypted_config); }
    catch (const std::exception&) { event("fixture_error"); result = 36; }
  }
  host.module->Destroy(); host.module = nullptr;
  deinit(); dlclose(library);
  event("finished", result);
  return result;
}
