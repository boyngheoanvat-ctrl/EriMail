#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

/// Call once on app start. Also registered by the dylib initializer.
FOUNDATION_EXPORT void ERIFirewallInstall(void);

/// General filter: HTTPS-only for ERI hosts + denylist from the app's Info.plist.
FOUNDATION_EXPORT BOOL ERIFirewallAllowsURL(NSURL * _Nullable url);

/// Strictly limited to ERI API hosts and HTTPS (for application-owned API traffic).
FOUNDATION_EXPORT BOOL ERIFirewallAllowsERIAPIURL(NSURL * _Nullable url);

/// Use this session for ERI-managed requests. Redirects are checked too.
FOUNDATION_EXPORT NSURLSession *ERIFirewallCreateSession(void);

/// Returns nil and calls completion with an error when the destination is denied.
FOUNDATION_EXPORT NSURLSessionDataTask * _Nullable
ERIFirewallDataTask(NSURLSession *session, NSURLRequest *request,
                    void (^completion)(NSData * _Nullable data,
                                       NSURLResponse * _Nullable response,
                                       NSError * _Nullable error));

NS_ASSUME_NONNULL_END
