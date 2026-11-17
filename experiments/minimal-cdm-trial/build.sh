#!/bin/zsh
set -eu
cd "${0:A:h}"
for target in cdm_probe decode_probe license_probe; do
  /usr/bin/xcrun clang++ -std=c++17 -Wall -Wextra -Werror -O1 -g \
    -I include "$target.cpp" -o "$target"
done
