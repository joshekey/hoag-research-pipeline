const {defineConfig}=require('@playwright/test');
module.exports=defineConfig({
 testDir:'./tests',testMatch:'workspace.browser.cjs',workers:1,timeout:90000,
 use:{baseURL:'http://127.0.0.1:8766',httpCredentials:{username:'admin',password:'synthetic-password'},viewport:{width:1600,height:1000},screenshot:'only-on-failure',launchOptions:{args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']}},
 webServer:{command:`"${process.env.PYTHON_EXECUTABLE||'python'}" tests/browser_server.py`,url:'http://127.0.0.1:8766/',timeout:60000,reuseExistingServer:false},
});
