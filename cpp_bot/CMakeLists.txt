cmake_minimum_required(VERSION 3.20)
project(kalshi_btc_mm_cpp LANGUAGES CXX)

# Needed for CMake 4.x with websocketpp's legacy CMake policy usage.
set(CMAKE_POLICY_VERSION_MINIMUM 3.5)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(FetchContent)

FetchContent_Declare(
  nlohmann_json
  GIT_REPOSITORY https://github.com/nlohmann/json.git
  GIT_TAG v3.11.3
)

FetchContent_Declare(
  websocketpp
  GIT_REPOSITORY https://github.com/zaphoyd/websocketpp.git
  GIT_TAG 0.8.2
)

FetchContent_Declare(
  asio
  GIT_REPOSITORY https://github.com/chriskohlhoff/asio.git
  GIT_TAG asio-1-30-2
)

FetchContent_MakeAvailable(nlohmann_json websocketpp asio)

find_package(OpenSSL REQUIRED)
find_package(CURL REQUIRED)
find_package(Threads REQUIRED)

add_executable(kalshi_bot_cpp
  src/main.cpp
  src/auth.cpp
  src/http_client.cpp
  src/market_finder.cpp
  src/execution_engine.cpp
  src/profit_calculator.cpp
  src/smart_monitor.cpp
)

target_include_directories(kalshi_bot_cpp PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/src
  ${websocketpp_SOURCE_DIR}
  ${asio_SOURCE_DIR}/asio/include
)

target_compile_definitions(kalshi_bot_cpp PRIVATE
  ASIO_STANDALONE
)

target_link_libraries(kalshi_bot_cpp PRIVATE
  nlohmann_json::nlohmann_json
  OpenSSL::Crypto
  OpenSSL::SSL
  CURL::libcurl
  Threads::Threads
)
