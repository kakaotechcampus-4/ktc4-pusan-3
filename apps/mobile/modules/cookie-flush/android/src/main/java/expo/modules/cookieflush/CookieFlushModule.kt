package expo.modules.cookieflush

import android.webkit.CookieManager
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

/**
 * 웹뷰 쿠키를 지금 디스크에 쓴다 (#469).
 *
 * 웹뷰는 쿠키를 주기적으로만 기록하고, react-native-webview 는 페이지 로드가 끝날 때만 flush 한다.
 * 앱을 켤 때의 refresh(#465)가 회전시킨 쿠키는 그 뒤에 생겨서, 바로 앱을 끄면 디스크에 직전
 * refresh 가 남고 서버(#468)가 재사용으로 보고 세션을 지운다.
 *
 * 🚨 쿠키 값을 읽거나 쓰지 않는다. 셸은 토큰을 들지 않는다 (apps/mobile/CLAUDE.md §1).
 *    여기서 하는 일은 기록 시점을 앞당기는 것 하나다.
 */
class CookieFlushModule : Module() {
  override fun definition() = ModuleDefinition {
    Name("CookieFlush")

    // 디스크 I/O 라 JS 스레드를 막지 않게 비동기로 둔다.
    AsyncFunction("flush") {
      CookieManager.getInstance().flush()
    }
  }
}
