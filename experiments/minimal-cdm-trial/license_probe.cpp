// Public Shaka test license exchange. Opaque messages travel only through an
// anonymous local socket to the HTTP broker, never stdout or a payload file.
#define CDM_PROBE_NO_MAIN
#include "cdm_probe.cpp"
#include <cerrno>
#include <deque>
#include <fstream>
#include <sys/socket.h>
#include <unistd.h>
#include "decrypt_samples.h"

static bool send_all(int fd, const void* data, size_t size) {
  const char* cursor = static_cast<const char*>(data);
  while (size) {
    auto count = send(fd, cursor, size, 0);
    if (count < 0 && errno == EINTR) continue;
    if (count <= 0) return false;
    cursor += count; size -= static_cast<size_t>(count);
  }
  return true;
}
static bool receive_all(int fd, void* data, size_t size) {
  char* cursor = static_cast<char*>(data);
  while (size) {
    auto count = recv(fd, cursor, size, 0);
    if (count < 0 && errno == EINTR) continue;
    if (count <= 0) return false;
    cursor += count; size -= static_cast<size_t>(count);
  }
  return true;
}

int main(int argc, char** argv) {
  if (argc != 4 && argc != 6) return 2;
  const int socket_fd = std::stoi(argv[3]);
  const int yes = 1;
  setsockopt(socket_fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, sizeof yes);
  timeval timeout{20, 0};
  setsockopt(socket_fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof timeout);
  std::ifstream source(argv[2], std::ios::binary);
  std::vector<uint8_t> pssh(std::istreambuf_iterator<char>{source}, {});
  if (pssh.empty() || pssh.size() > 4096) return 30;
  void* library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  if (!library) return 10;
  auto init = reinterpret_cast<void (*)()>(dlsym(library, "InitializeCdmModule_4"));
  auto deinit = reinterpret_cast<void (*)()>(dlsym(library, "DeinitializeCdmModule"));
  auto create = reinterpret_cast<Create>(dlsym(library, "CreateCdmInstance"));
  if (!init || !deinit || !create) return 11;
  init();
  Host<cdm::Host_10, cdm::ContentDecryptionModule_10> host;
  constexpr char system[] = "com.widevine.alpha";
  host.module = static_cast<cdm::ContentDecryptionModule_10*>(create(10, system, sizeof(system)-1, &decltype(host)::get, &host));
  if (!host.module) { deinit(); dlclose(library); return 20; }
  std::string session;
  std::deque<std::vector<uint8_t>> requests;
  uint32_t usable = 0;
  bool rejected = false;
  host.new_session = [&](const char* id, uint32_t size) { session.assign(id, size); };
  host.session_message = [&](cdm::MessageType type, const char* message, uint32_t size) {
    if (type != cdm::kLicenseRequest || size > 1024u * 1024u) { rejected = true; return; }
    requests.emplace_back(message, message + size);
  };
  host.promise_rejected = [&](uint32_t) { rejected = true; };
  host.usable_keys = [&](uint32_t count) { usable = count; };
  host.module->Initialize(false, false, false);
  auto deadline = Clock::now() + std::chrono::seconds(10);
  while (host.initialized.load() < 0 && Clock::now() < deadline) {
    host.pump(); std::this_thread::sleep_for(std::chrono::milliseconds(5));
  }
  int result = 21;
  if (host.initialized.load() == 1) {
    host.module->CreateSessionAndGenerateRequest(1, cdm::kTemporary, cdm::kCenc,
                                               pssh.data(), static_cast<uint32_t>(pssh.size()));
    result = 42;
    uint32_t exchanges = 0;
    deadline = Clock::now() + std::chrono::seconds(45);
    while (!rejected && usable == 0 && Clock::now() < deadline) {
      host.pump();
      if (requests.empty()) { std::this_thread::sleep_for(std::chrono::milliseconds(10)); continue; }
      if (exchanges++ >= 3 || session.empty()) { result = 43; break; }
      auto request = std::move(requests.front()); requests.pop_front();
      uint32_t size = static_cast<uint32_t>(request.size());
      if (!send_all(socket_fd, &size, 4) || !send_all(socket_fd, request.data(), size)
          || !receive_all(socket_fd, &size, 4) || size == 0 || size > 1024u * 1024u) {
        event("license_transport_failed"); result = 44; break;
      }
      std::vector<uint8_t> response(size);
      if (!receive_all(socket_fd, response.data(), size)) { result = 44; break; }
      host.module->UpdateSession(1 + exchanges, session.data(), static_cast<uint32_t>(session.size()),
                                 response.data(), static_cast<uint32_t>(response.size()));
    }
    if (rejected) result = 41;
    if (usable > 0) result = 0;
    event("usable_key_count", usable);
    event("license_exchange_count", exchanges);
    if (result == 0 && argc == 6) {
      try { result = decrypt_public_samples(host.module, argv[4], argv[5]); }
      catch (const std::exception&) { event("fixture_error"); result = 54; }
    }
    if (!session.empty()) host.module->CloseSession(100, session.data(), static_cast<uint32_t>(session.size()));
  }
  // Destroy before captured callback state goes out of scope.
  host.module->Destroy(); host.module = nullptr;
  deinit(); dlclose(library);
  event("finished", result);
  return result;
}
