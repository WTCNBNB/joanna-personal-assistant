#!/usr/bin/env sh

# Source this file before building or using adb from a plain shell:
# . ./dev-env.sh

if [ -z "${JAVA_HOME:-}" ]; then
  if [ -x "/usr/libexec/java_home" ]; then
    JAVA_HOME="$(/usr/libexec/java_home -v 17 2>/dev/null || true)"
  fi
  if [ -z "${JAVA_HOME:-}" ] && [ -x "/Applications/HBuilderX.app/Contents/HBuilderX/plugins/amazon-corretto/bin/java" ]; then
    JAVA_HOME="/Applications/HBuilderX.app/Contents/HBuilderX/plugins/amazon-corretto"
  fi
  export JAVA_HOME
fi

if [ -n "${JAVA_HOME:-}" ]; then
  export PATH="$JAVA_HOME/bin:$PATH"
fi

if [ -d "/Applications/HBuilderX.app/Contents/HBuilderX/plugins/launcher-tools/tools/adbs" ]; then
  export PATH="/Applications/HBuilderX.app/Contents/HBuilderX/plugins/launcher-tools/tools/adbs:$PATH"
fi

if [ -z "${ANDROID_HOME:-}" ]; then
  export ANDROID_HOME="$HOME/Library/Android/sdk"
fi
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"
