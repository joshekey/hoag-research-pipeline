/* OHIF 3.9.2 — HOAG clinical single-study pilot.
 * Install as /opt/hoag-research/ohif-assets/app-config.js ONLY after
 * clinical gateway approval and single-study allowlist setup.
 * No external imaging endpoints or token prompts.
 */
window.config = {
  routerBasename: '/',
  httpErrorHandler: function () {
    // Never print source URLs or patient identifiers to console.
    let message = document.getElementById('hoag-image-error');
    if (!message) {
      message = document.createElement('div');
      message.id = 'hoag-image-error';
      message.setAttribute('role', 'alert');
      message.style.cssText = 'position:fixed;bottom:12px;left:12px;right:12px;z-index:99999;background:#fff4e5;color:#503000;padding:16px;border-radius:8px';
      document.body.appendChild(message);
    }
    message.textContent = 'An image could not be loaded. Return to the worklist and check source availability. Do not mark this study reviewed until all images are available.';
  },
  extensions: [],
  modes: [],
  showStudyList: false,
  defaultDataSourceName: 'hoag-clinical',
  dataSources: [
    {
      namespace: '@ohif/extension-default.dataSourcesModule.dicomweb',
      sourceName: 'hoag-clinical',
      configuration: {
        friendlyName: 'HOAG Restricted Pilot',
        name: 'HOAG-RESTRICTED',
        qidoRoot: '/ohif/dicomweb',
        wadoRoot: '/ohif/dicomweb',
        wadoUriRoot: '/ohif/dicomweb',
        qidoSupportsIncludeField: false,
        supportsReject: false,
        supportsStow: false,
        supportsFuzzyMatching: false,
        supportsWildcard: false,
        imageRendering: 'wadors',
        thumbnailRendering: 'wadors',
        enableStudyLazyLoad: true,
        staticWado: false,
        omitQuotationForMultipartRequest: true
      }
    }
  ]
};
