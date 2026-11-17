// Public CDM Decrypt API. Operates only on bounded test samples supplied locally.
#include <array>
#include <stdexcept>

class ClearBlock final : public cdm::DecryptedBlock {
 public:
  ~ClearBlock() override { if (buffer_) buffer_->Destroy(); }
  void SetDecryptedBuffer(cdm::Buffer* buffer) override {
    if (buffer_ && buffer_ != buffer) buffer_->Destroy();
    buffer_ = buffer;
  }
  cdm::Buffer* DecryptedBuffer() override { return buffer_; }
  void SetTimestamp(int64_t value) override { timestamp_ = value; }
  int64_t Timestamp() const override { return timestamp_; }
 private:
  cdm::Buffer* buffer_ = nullptr;
  int64_t timestamp_ = 0;
};

template<class T> static T fixture_read(std::ifstream& input) {
  T value{};
  if (!input.read(reinterpret_cast<char*>(&value), sizeof value)) throw std::runtime_error("truncated fixture");
  return value;
}

static std::vector<uint8_t> fixture_bytes(std::ifstream& input, uint32_t size) {
  if (size > 2u * 1024u * 1024u) throw std::runtime_error("fixture bounds");
  std::vector<uint8_t> data(size);
  if (size && !input.read(reinterpret_cast<char*>(data.data()), size)) throw std::runtime_error("truncated fixture");
  return data;
}

static int decrypt_public_samples(cdm::ContentDecryptionModule_10* module,
                                  const char* input_path, const char* output_path) {
  std::ifstream input(input_path, std::ios::binary);
  const auto magic = fixture_bytes(input, 8);
  if (std::memcmp(magic.data(), "CENCTST1", 8) != 0) return 50;
  const uint32_t count = fixture_read<uint32_t>(input);
  if (count == 0 || count > 1000) return 50;
  std::ofstream output(output_path, std::ios::binary);
  if (!output) return 51;
  output.write("CLEARTST", 8);
  output.write(reinterpret_cast<const char*>(&count), 4);
  uint32_t counts[2]{};
  uint64_t bytes = 0;
  for (uint32_t index = 0; index < count; ++index) {
    const auto track = fixture_read<uint32_t>(input);
    if (track > 1) return 50;
    cdm::InputBuffer_2 buffer{};
    buffer.timestamp = fixture_read<int64_t>(input);
    auto kid = fixture_bytes(input, 16);
    const auto iv_size = fixture_read<uint32_t>(input);
    if (iv_size != 8 && iv_size != 16) return 50;
    auto iv = fixture_bytes(input, iv_size);
    const auto sub_count = fixture_read<uint32_t>(input);
    if (sub_count > 256) return 50;
    std::vector<cdm::SubsampleEntry> subs(sub_count);
    uint64_t total = 0;
    for (auto& sub : subs) {
      sub.clear_bytes = fixture_read<uint32_t>(input);
      sub.cipher_bytes = fixture_read<uint32_t>(input);
      total += uint64_t(sub.clear_bytes) + sub.cipher_bytes;
    }
    auto data = fixture_bytes(input, fixture_read<uint32_t>(input));
    if (data.empty() || (sub_count && total != data.size())) return 50;
    buffer.encryption_scheme = cdm::EncryptionScheme::kCenc;
    buffer.data = data.data(); buffer.data_size = static_cast<uint32_t>(data.size());
    buffer.key_id = kid.data(); buffer.key_id_size = static_cast<uint32_t>(kid.size());
    buffer.iv = iv.data(); buffer.iv_size = iv_size;
    buffer.subsamples = subs.empty() ? nullptr : subs.data(); buffer.num_subsamples = sub_count;
    ClearBlock block;
    const auto status = module->Decrypt(buffer, &block);
    if (status != cdm::kSuccess) {
      event("decrypt_error_status", status); event("decrypt_error_track", track);
      event("decrypt_error_sample", index); return 52;
    }
    auto* clear = block.DecryptedBuffer();
    if (!clear || clear->Size() != data.size() || clear->Size() > clear->Capacity()) return 53;
    const uint32_t size = clear->Size();
    output.write(reinterpret_cast<const char*>(&size), 4);
    output.write(reinterpret_cast<const char*>(clear->Data()), size);
    if (!output) return 51;
    ++counts[track]; bytes += size;
  }
  if (input.peek() != std::char_traits<char>::eof()) return 50;
  output.close();
  event("decrypted_video_samples", counts[0]); event("decrypted_audio_samples", counts[1]);
  event("decrypted_compressed_bytes", bytes);
  return output.fail() ? 51 : 0;
}
