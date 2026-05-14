interface ISiteMetadataResult {
  siteTitle: string;
  siteUrl: string;
  description: string;
  keywords: string;
  logo: string;
  navLinks: {
    name: string;
    url: string;
  }[];
}

const data: ISiteMetadataResult = {
  siteTitle: '晚风踏星河的运动记录',
  siteUrl: 'https://run.20260419.xyz',
  logo: 'https://pub-91575f75878d40de9acc9395c2ce673a.r2.dev/%E9%87%91%E6%99%BA%E5%AA%9B.png',
  description: 'Personal site and blog',
  keywords: 'workouts, running, cycling, riding, roadtrip, hiking, swimming',
  navLinks: [],
};

export default data;
