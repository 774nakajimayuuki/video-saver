// Diagnostic host for the public Chromium CDM ABI. No private TunePat API calls.
#include "content_decryption_module.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <dlfcn.h>
#include <functional>
#include <mutex>
#include <new>
#include <string>
#include <thread>
#include <vector>

using Clock = std::chrono::steady_clock;

static void event(const char* name, long long value = 0) {
  std::printf("CDM_PROBE {\"event\":\"%s\",\"value\":%lld}\n", name, value);
  std::fflush(stdout);
}

class Buffer final : public cdm::Buffer {
 public:
  explicit Buffer(uint32_t capacity) : bytes_(capacity) {}
  void Destroy() override { delete this; }
  uint32_t Capacity() const override { return static_cast<uint32_t>(bytes_.size()); }
  uint8_t* Data() override { return bytes_.data(); }
  void SetSize(uint32_t size) override { size_ = std::min(size, Capacity()); }
  uint32_t Size() const override { return size_; }
 private:
  std::vector<uint8_t> bytes_;
  uint32_t size_ = 0;
};

template<class Interface> class MetricsHost : public Interface {
 public:
  void ReportMetrics(cdm::MetricName, uint64_t) override {}
};
template<> class MetricsHost<cdm::Host_10> : public cdm::Host_10 {};

template<class HostInterface, class Module>
class Host final : public MetricsHost<HostInterface> {
 public:
  using KeyStatus = std::conditional_t<HostInterface::kVersion == 12, cdm::KeyStatus_2, cdm::KeyStatus>;
  using KeyInfo = std::conditional_t<HostInterface::kVersion == 12, cdm::KeyInformation_2, cdm::KeyInformation>;
  Module* module = nullptr;
  std::atomic<int> initialized{-1};
  std::atomic<int> decoder_initialized{-1};
  std::function<void(const char*, uint32_t)> new_session;
  std::function<void(cdm::MessageType, const char*, uint32_t)> session_message;
  std::function<void(uint32_t)> promise_resolved;
  std::function<void(uint32_t)> promise_rejected;
  std::function<void(uint32_t)> usable_keys;

  static void* get(int version, void* user) {
    event("host_interface_requested", version);
    return version == HostInterface::kVersion
        ? static_cast<HostInterface*>(static_cast<Host*>(user)) : nullptr;
  }
  cdm::Buffer* Allocate(uint32_t size) override {
    if (size == 0 || size > 64u * 1024u * 1024u) return nullptr;
    try { return new Buffer(size); } catch (const std::bad_alloc&) { return nullptr; }
  }
  void SetTimer(int64_t milliseconds, void* context) override {
    schedule(std::max<int64_t>(0, milliseconds), [this, context] {
      if (module) module->TimerExpired(context);
    });
  }
  cdm::Time GetCurrentWallTime() override {
    return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
  }
  void OnInitialized(bool success) override {
    initialized.store(success ? 1 : 0);
    event("initialized", success);
  }
  // Version 12 changes the key-status types, while the other callbacks match.
  void OnResolveKeyStatusPromise(uint32_t, KeyStatus status) override {
    event("policy_status", static_cast<uint32_t>(status));
  }
  void OnResolveNewSessionPromise(uint32_t, const char* id, uint32_t size) override {
    event("session_created_length", size);
    if (new_session) new_session(id, size);
  }
  void OnResolvePromise(uint32_t id) override {
    event("promise_resolved", id);
    if (promise_resolved) promise_resolved(id);
  }
  void OnRejectPromise(uint32_t id, cdm::Exception exception, uint32_t code,
                       const char*, uint32_t size) override {
    event("promise_rejected", static_cast<uint32_t>(exception));
    event("system_code", code);
    event("error_message_length", size);
    if (promise_rejected) promise_rejected(id);
  }
  void OnSessionMessage(const char*, uint32_t, cdm::MessageType type,
                        const char* message, uint32_t size) override {
    // Payload and session ID deliberately omitted.
    event("session_message_type", static_cast<uint32_t>(type));
    event("session_message_length", size);
    if (session_message) session_message(type, message, size);
  }
  void OnSessionKeysChange(const char*, uint32_t, bool usable,
                          const KeyInfo* keys, uint32_t count) override {
    event("keys_usable", usable); event("key_count", count);
    uint32_t usable_count = 0;
    for (uint32_t i = 0; i < count; ++i) {
      if (static_cast<uint32_t>(keys[i].status) == 0) ++usable_count;
    }
    if (usable_keys) usable_keys(usable_count);
  }
  void OnExpirationChange(const char*, uint32_t, cdm::Time) override {}
  void OnSessionClosed(const char*, uint32_t) override { event("session_closed"); }
  void SendPlatformChallenge(const char*, uint32_t, const char*, uint32_t) override {
    event("platform_challenge_unavailable");
    schedule(0, [this] {
      const cdm::PlatformChallengeResponse unavailable{};
      if (module) module->OnPlatformChallengeResponse(unavailable);
    });
  }
  void EnableOutputProtection(uint32_t) override { event("output_protection_unavailable"); }
  void QueryOutputProtectionStatus() override {
    event("output_protection_query_unavailable");
    schedule(0, [this] {
      if (module) module->OnQueryOutputProtectionStatus(cdm::kQueryFailed, 0, 0);
    });
  }
  void OnDeferredInitializationDone(cdm::StreamType, cdm::Status status) override {
    decoder_initialized.store(static_cast<int>(status));
    event("decoder_deferred_result", static_cast<uint32_t>(status));
  }
  cdm::FileIO* CreateFileIO(cdm::FileIOClient*) override {
    event("persistent_storage_disabled"); return nullptr;
  }
  void RequestStorageId(uint32_t version) override {
    event("storage_id_unavailable");
    schedule(0, [this, version] {
      if (module) module->OnStorageId(version, nullptr, 0);
    });
  }
  void pump() {
    std::vector<std::function<void()>> ready;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      const auto now = Clock::now();
      auto it = timers_.begin();
      while (it != timers_.end()) {
        if (it->first <= now) {
          ready.push_back(std::move(it->second));
          it = timers_.erase(it);
        } else ++it;
      }
    }
    for (auto& task : ready) task();
  }
 private:
  void schedule(int64_t delay, std::function<void()> task) {
    std::lock_guard<std::mutex> lock(mutex_);
    // Long timers are irrelevant to this bounded initialization probe.
    delay = std::min<int64_t>(delay, 60 * 1000);
    timers_.emplace_back(Clock::now() + std::chrono::milliseconds(delay), std::move(task));
  }
  std::mutex mutex_;
  std::vector<std::pair<Clock::time_point, std::function<void()>>> timers_;
};

using Create = void* (*)(int, const char*, uint32_t, GetCdmHostFunc, void*);

#ifndef CDM_PROBE_NO_MAIN
template<class HostInterface, class Module>
int run(Create create) {
  Host<HostInterface, Module> host;
  constexpr char key_system[] = "com.widevine.alpha";
  event("create_begin", Module::kVersion);
  host.module = static_cast<Module*>(create(Module::kVersion, key_system,
      sizeof(key_system) - 1, &Host<HostInterface, Module>::get, &host));
  event("instance_created", host.module != nullptr);
  if (!host.module) return 20;
  event("initialize_begin");
  host.module->Initialize(false, false, false);
  const auto deadline = Clock::now() + std::chrono::seconds(10);
  while (host.initialized.load() < 0 && Clock::now() < deadline) {
    host.pump();
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  const int result = host.initialized.load();
  if (result < 0) event("initialize_timeout");
  event("destroy_begin");
  host.module->Destroy();
  host.module = nullptr;
  event("destroy_complete");
  return result == 1 ? 0 : result == 0 ? 21 : 22;
}

int main(int argc, char** argv) {
  if (argc != 3) return 2;
  const std::string version(argv[2]);
  if (version != "10" && version != "11" && version != "12") return 2;
  event("load_begin");
  void* library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  if (!library) { event("load_failed"); return 10; }
  event("load_success");
  auto initialize = reinterpret_cast<void (*)()>(dlsym(library, "InitializeCdmModule_4"));
  auto deinitialize = reinterpret_cast<void (*)()>(dlsym(library, "DeinitializeCdmModule"));
  auto create = reinterpret_cast<Create>(dlsym(library, "CreateCdmInstance"));
  if (!initialize || !deinitialize || !create) { event("exports_missing"); dlclose(library); return 11; }
  event("module_initialize_begin");
  initialize();
  event("module_initialize_complete");
  int result = version == "10" ? run<cdm::Host_10, cdm::ContentDecryptionModule_10>(create)
      : version == "11" ? run<cdm::Host_11, cdm::ContentDecryptionModule_11>(create)
      : run<cdm::Host_12, cdm::ContentDecryptionModule_12>(create);
  event("module_deinitialize_begin");
  deinitialize();
  event("module_deinitialize_complete");
  dlclose(library);
  event("finished", result);
  return result;
}
#endif
