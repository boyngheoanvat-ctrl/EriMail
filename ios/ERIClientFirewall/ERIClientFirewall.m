#import "ERIClientFirewall.h"
#import <dispatch/dispatch.h>

static BOOL ERIHostMatches(NSString *host, NSString *rule) {
    if (!host.length || !rule.length) return NO;
    NSString *normalized = [rule.lowercaseString stringByTrimmingCharactersInSet:
        [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if ([normalized hasPrefix:@"*."]) {
        NSString *suffix = [normalized substringFromIndex:1]; // ".example.com"
        return [host hasSuffix:suffix] && host.length > suffix.length;
    }
    return [host isEqualToString:normalized];
}

static BOOL ERIHostInList(NSString *host, NSArray *values) {
    for (id value in values) {
        if ([value isKindOfClass:[NSString class]] && ERIHostMatches(host, value)) return YES;
    }
    return NO;
}

static NSArray *ERIPlistHostArray(NSString *key) {
    id raw = [[NSBundle mainBundle] objectForInfoDictionaryKey:key];
    return [raw isKindOfClass:[NSArray class]] ? raw : @[];
}

static NSString *ERINormalizedHost(NSURL *url) {
    return url.host.lowercaseString ?: @"";
}

static BOOL ERIIsManagedHost(NSString *host) {
    return [host isEqualToString:@"eri-login-api.onrender.com"] ||
           [host isEqualToString:@"eri-api-dag8.onrender.com"];
}

static BOOL ERIIsSecureURL(NSURL *url) {
    if (!url || ![url.scheme.lowercaseString isEqualToString:@"https"]) return NO;
    if (url.user.length || url.password.length) return NO;
    return (!url.port || url.port.integerValue == 443);
}

BOOL ERIFirewallAllowsURL(NSURL *url) {
    if (!url) return NO;
    NSString *host = ERINormalizedHost(url);
    if (!host.length) return NO;
    // Do not alter unrelated app/game networking. Check only configured blocks
    // and enforce secure transport for ERI-owned service hosts.
    if (ERIHostInList(host, ERIPlistHostArray(@"ERIFirewallBlockedHosts"))) return NO;
    if (ERIIsManagedHost(host) && !ERIIsSecureURL(url)) return NO;
    return YES;
}

BOOL ERIFirewallAllowsERIAPIURL(NSURL *url) {
    if (!ERIFirewallAllowsURL(url) || !ERIIsSecureURL(url)) return NO;
    return ERIIsManagedHost(ERINormalizedHost(url));
}

static NSError *ERIDeniedError(void) {
    return [NSError errorWithDomain:NSURLErrorDomain
                               code:NSURLErrorCannotConnectToHost
                           userInfo:@{NSLocalizedDescriptionKey: @"ERI Firewall: blocked destination"}];
}

@interface ERIBlockProtocol : NSURLProtocol
@end

@implementation ERIBlockProtocol
+ (BOOL)canInitWithRequest:(NSURLRequest *)request {
    return request.URL && !ERIFirewallAllowsURL(request.URL);
}
+ (NSURLRequest *)canonicalRequestForRequest:(NSURLRequest *)request {
    return request;
}
- (void)startLoading {
    [self.client URLProtocol:self didFailWithError:ERIDeniedError()];
}
- (void)stopLoading {}
@end

@interface ERIRedirectGuard : NSObject <NSURLSessionTaskDelegate>
@end

@implementation ERIRedirectGuard
- (void)URLSession:(NSURLSession *)session
              task:(NSURLSessionTask *)task
willPerformHTTPRedirection:(NSHTTPURLResponse *)response
        newRequest:(NSURLRequest *)request
 completionHandler:(void (^)(NSURLRequest * _Nullable))completionHandler {
    completionHandler(ERIFirewallAllowsERIAPIURL(request.URL) ? request : nil);
}
@end

void ERIFirewallInstall(void) {
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        // Best-effort registration for Foundation's URL loading system.
        // This does not cover native socket traffic or background URLSession.
        [NSURLProtocol registerClass:[ERIBlockProtocol class]];
    });
}

NSURLSession *ERIFirewallCreateSession(void) {
    ERIFirewallInstall();
    NSURLSessionConfiguration *config = [NSURLSessionConfiguration ephemeralSessionConfiguration];
    config.protocolClasses = @[[ERIBlockProtocol class]];
    config.URLCache = nil;
    return [NSURLSession sessionWithConfiguration:config
                                        delegate:[ERIRedirectGuard new]
                                   delegateQueue:nil];
}

NSURLSessionDataTask *ERIFirewallDataTask(NSURLSession *session,
                                         NSURLRequest *request,
                                         void (^completion)(NSData *, NSURLResponse *, NSError *)) {
    if (!session || !ERIFirewallAllowsERIAPIURL(request.URL)) {
        if (completion) {
            dispatch_async(dispatch_get_main_queue(), ^{
                completion(nil, nil, ERIDeniedError());
            });
        }
        return nil;
    }
    return [session dataTaskWithRequest:request completionHandler:completion];
}

__attribute__((constructor))
static void ERIFirewallOnLoad(void) {
    @autoreleasepool { ERIFirewallInstall(); }
}
